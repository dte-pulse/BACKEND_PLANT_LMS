from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Pulse LMS API'
    app_env: str = 'development'
    app_debug: bool = True
    app_host: str = '0.0.0.0'
    app_port: int = 8000
    api_v1_prefix: str = '/api/v1'
    secret_key: str = 'change-me-super-secret'
    access_token_expire_minutes: int = 60
    database_url: str = 'sqlite:///./pulse_lms.db'
    redis_url: str = 'redis://localhost:6379/0'
    celery_broker_url: str = 'redis://localhost:6379/1'
    celery_result_backend: str = 'redis://localhost:6379/2'
    cors_origins: list[str] = Field(default_factory=lambda: ['http://localhost:5173', 'http://127.0.0.1:5173'])
    upload_dir: str = './uploads'
    aws_access_key_id: str = 'change-me'
    aws_secret_access_key: str = 'change-me'
    aws_region: str = 'us-east-1'
    s3_bucket_name: str = 'hrappmodule'
    s3_endpoint_url: str | None = None
    gemini_api_key: str = 'change-me'


settings = Settings()
