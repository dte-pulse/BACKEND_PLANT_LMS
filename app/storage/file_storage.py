import logging
from pathlib import Path
from uuid import uuid4
import boto3
from botocore.exceptions import ClientError
from fastapi import UploadFile

from app.core.config import settings

logger = logging.getLogger(__name__)

class FileStorageService:
    def __init__(self):
        self.base_dir = Path(settings.upload_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        from app.core.s3 import get_s3_client
        self.s3_client = get_s3_client()
        self.use_s3 = self.s3_client is not None

    def save_upload(self, upload: UploadFile) -> tuple[str, str, str]:
        suffix = Path(upload.filename).suffix.lower()
        stored_name = f"{uuid4().hex}{suffix}"
        file_type = suffix.replace('.', '')
        
        # Read content
        content = upload.file.read()
        
        # Always save to local cache
        file_path = self.base_dir / stored_name
        file_path.write_bytes(content)
        
        if self.use_s3:
            try:
                # Upload to S3
                logger.info(f"Uploading {stored_name} to S3 bucket {settings.s3_bucket_name}...")
                self.s3_client.put_object(
                    Bucket=settings.s3_bucket_name,
                    Key=stored_name,
                    Body=content,
                    ContentType=upload.content_type or 'application/octet-stream'
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
