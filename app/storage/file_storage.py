import logging
from pathlib import Path
from uuid import uuid4
import boto3
from botocore.exceptions import ClientError
from fastapi import UploadFile

from app.core.config import settings

logger = logging.getLogger(__name__)

# Extension allowlist for general uploads (evidence, documents).
ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.png', '.jpg', '.jpeg', '.webp'}
# PPWEC media allowlist (§23 voice-over / captions / videos).
PPWEC_MEDIA_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.m4a', '.webm', '.mp4', '.vtt'}
# MIME types we consider safe to store verbatim for browser rendering.
_SAFE_CONTENT_TYPES = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.ogg': 'audio/ogg',
    '.m4a': 'audio/mp4',
    '.webm': 'video/webm',
    '.mp4': 'video/mp4',
    '.vtt': 'text/vtt',
}

class UploadValidationError(Exception):
    """Raised when an uploaded file fails size, extension or content validation."""


def _check_size(upload: UploadFile) -> bytes:
    max_bytes = int(settings.max_upload_bytes)
    content = upload.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise UploadValidationError(
            f'File too large. Maximum allowed size is {max_bytes // (1024 * 1024)} MB.'
        )
    if not content:
        raise UploadValidationError('Empty file uploads are not allowed.')
    return content


def _check_extension(stored_suffix: str) -> None:
    if stored_suffix not in ALLOWED_EXTENSIONS and stored_suffix != '.csv':
        raise UploadValidationError(
            f'File type "{stored_suffix}" is not allowed. '
            f'Allowed types: {", ".join(sorted(ALLOWED_EXTENSIONS | {".csv"}))}.'
        )


def _check_magic_bytes(content: bytes, stored_suffix: str) -> None:
    """Reject files whose content does not match their claimed type."""
    if stored_suffix == '.csv':
        return  # csv validated by caller (users import) / treated as text
    if stored_suffix == '.vtt':
        text = content[:4096].lstrip()
        return_ok = text.startswith(b'WEBVTT') or text.startswith(b'NOTE')
        if not return_ok:
            raise UploadValidationError('Invalid WebVTT caption file — must start with WEBVTT.')
        return
    if stored_suffix in ('.mp3', '.wav', '.ogg', '.m4a', '.webm', '.mp4'):
        # Audio/video containers: id3/MP3 sync, RIFF/WAVE, OggS, M4A, WebM/Matroska, MP4.
        if content[:3] == b'ID3' or (len(content) > 2 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0):
            return
        if content.startswith(b'RIFF'):
            return
        if content.startswith(b'OggS'):
            return
        if content[4:8] in (b'ftyp',):  # MP4 / M4A
            return
        if content.startswith((b'\x1aE\xdf\xa3', b'\x0D\x38\x2B\x2B')):  # WebM / Matroska
            return
        raise UploadValidationError(
            'File content does not match its audio/video extension.'
        )
    if content.startswith(b'%PDF'):
        return
    if content.startswith(b'PK\x03\x04'):
        return  # docx
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        return
    if content.startswith(b'\xff\xd8\xff'):
        return
    if content.startswith((b'RIFF', b'WEBP')):
        return
    raise UploadValidationError(
        'File content does not match its extension. Only PDF, DOCX, CSV and '
        'common image formats are accepted.'
    )


def validate_upload(content: bytes, filename: str) -> None:
    """Shared validation used by all upload paths (VULN-007)."""
    suffix = Path(filename or '').suffix.lower()
    _check_extension(suffix)
    _check_magic_bytes(content, suffix)


class FileStorageService:
    def __init__(self):
        self.base_dir = Path(settings.upload_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        from app.core.s3 import get_s3_client
        self.s3_client = get_s3_client()
        self.use_s3 = self.s3_client is not None

    def save_upload(self, upload: UploadFile) -> tuple[str, str, str]:
        content = _check_size(upload)
        suffix = Path(upload.filename or '').suffix.lower()
        _check_extension(suffix)
        _check_magic_bytes(content, suffix)

        stored_name = f"{uuid4().hex}{suffix}"
        file_type = suffix.replace('.', '')

        # Always save to local cache
        file_path = self.base_dir / stored_name
        file_path.write_bytes(content)

        if self.use_s3:
            try:
                # Upload to S3 with a server-controlled Content-Type derived from
                # the validated extension — never the client-supplied header.
                content_type = _SAFE_CONTENT_TYPES.get(suffix, 'application/octet-stream')
                logger.info(f"Uploading {stored_name} to S3 bucket {settings.s3_bucket_name}...")
                self.s3_client.put_object(
                    Bucket=settings.s3_bucket_name,
                    Key=stored_name,
                    Body=content,
                    ContentType=content_type,
                )

                # Formulate S3 URL
                if settings.s3_endpoint_url:
                    file_url = f"{settings.s3_endpoint_url}/{settings.s3_bucket_name}/{stored_name}"
                else:
                    file_url = f"https://{settings.s3_bucket_name}.s3.{settings.aws_region}.amazonaws.com/{stored_name}"

                return stored_name, file_type, file_url
            except ClientError as e:
                logger.error(f"S3 upload failed: {e}. Falling back to local storage URL.")
                # Fall back to returning local path

        file_url = str(file_path)
        return stored_name, file_type, file_url

    def save_ppwec_media(self, upload: UploadFile) -> tuple[str, str, str]:
        """Store a validated PPWEC audio/video/caption asset.

        Returns (stored_name, media_kind, url). Local disk + S3, mirroring
        save_upload(); uses the PPWEC media allowlist and container magic-byte
        checks. media_kind ∈ audio | video | caption.
        """
        suffix = Path(upload.filename or '').suffix.lower()
        if suffix not in PPWEC_MEDIA_EXTENSIONS:
            raise UploadValidationError(
                f'Media type "{suffix}" is not allowed. Allowed: '
                f'{", ".join(sorted(PPWEC_MEDIA_EXTENSIONS))}.'
            )
        content = _check_size(upload)
        _check_magic_bytes(content, suffix)

        stored_name = f"ppwec/{uuid4().hex}{suffix}"
        media_kind = ('caption' if suffix == '.vtt'
                      else 'video' if suffix in ('.webm', '.mp4')
                      else 'audio')

        file_path = self.base_dir / stored_name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)

        if self.use_s3:
            try:
                content_type = _SAFE_CONTENT_TYPES.get(suffix, 'application/octet-stream')
                self.s3_client.put_object(
                    Bucket=settings.s3_bucket_name,
                    Key=stored_name,
                    Body=content,
                    ContentType=content_type,
                )
                if settings.s3_endpoint_url:
                    file_url = f"{settings.s3_endpoint_url}/{settings.s3_bucket_name}/{stored_name}"
                else:
                    file_url = f"https://{settings.s3_bucket_name}.s3.{settings.aws_region}.amazonaws.com/{stored_name}"
                return stored_name, media_kind, file_url
            except ClientError as e:
                logger.error(f'PPWEC media S3 upload failed: {e}. Falling back to local storage URL.')

        file_url = str(file_path)
        return stored_name, media_kind, file_url

    def get_presigned_url(self, stored_name: str, expiration: int = 3600) -> str:
        if not self.use_s3:
            file_path = self.base_dir / stored_name
            return str(file_path)

        try:
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': settings.s3_bucket_name, 'Key': stored_name},
                ExpiresIn=expiration
            )
            return url
        except ClientError as e:
            logger.error(f"Failed to generate presigned URL: {e}")
            return ""

    def get_file_content(self, stored_name: str) -> bytes:
        if not self.use_s3:
            file_path = self.base_dir / stored_name
            return file_path.read_bytes()

        try:
            response = self.s3_client.get_object(Bucket=settings.s3_bucket_name, Key=stored_name)
            return response['Body'].read()
        except ClientError as e:
            logger.error(f"Failed to get object from S3: {e}")
            raise ValueError(f"Could not retrieve file from S3: {e}")
