import logging
import boto3
from botocore.exceptions import ClientError
from app.core.config import settings

logger = logging.getLogger(__name__)


def get_s3_client():
    """
    Initialize and return a boto3 S3 client if valid credentials exist,
    otherwise return None.
    """
    use_s3 = (
        settings.aws_access_key_id 
        and settings.aws_access_key_id not in ('change-me', 'replace-me', '')
    )
    if not use_s3:
        return None

    try:
        return boto3.client(
            's3',
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
            endpoint_url=settings.s3_endpoint_url
        )
    except Exception as e:
        logger.error(f"Failed to initialize unified S3 client: {e}")
        return None
