import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Pulse LMS API'
    app_env: str = 'development'
    # Secure default: debug mode must be opted INTO per environment, not left on.
    app_debug: bool = False
    app_host: str = '0.0.0.0'
    app_port: int = 8000
    api_v1_prefix: str = '/api/v1'
    secret_key: str = 'change-me-super-secret'
    access_token_expire_minutes: int = 60
    database_url: str = 'sqlite:///./pulse_lms.db'
    # Idle-ping threshold: ping a pooled DB connection only when it has been
    # unused for at least this many seconds. Replaces pool_pre_ping, which paid
    # a remote-DB round-trip on EVERY checkout (~400ms on cloud Postgres).
    db_idle_ping_seconds: int = 30
    redis_url: str = 'redis://localhost:6379/0'
    celery_broker_url: str = 'redis://localhost:6379/1'
    celery_result_backend: str = 'redis://localhost:6379/2'
    cors_origins: list[str] = Field(default_factory=lambda: ['http://localhost:5173', 'http://127.0.0.1:5173'])
    upload_dir: str = './uploads'
    # Hard cap on any single uploaded file (25 MiB) — guards against storage
    # exhaustion / memory DoS on the upload endpoints.
    max_upload_bytes: int = 26_214_144
    aws_access_key_id: str = 'change-me'
    aws_secret_access_key: str = 'change-me'
    aws_region: str = 'us-east-1'
    s3_bucket_name: str = 'hrappmodule'
    s3_endpoint_url: str | None = None
    gemini_api_key: str = 'change-me'

    # ── Retrieval tuning ──────────────────────────────────────────────────
    # R-3: rerank the RRF-fused candidate pool with a cross-encoder
    # (ms-marco-MiniLM-L-6-v2) before returning top_k. Degrades gracefully to
    # plain RRF order when sentence-transformers is unavailable. Adds one GPU/CPU
    # inference (~10-30ms) per QA query — disable on CPU-constrained deploys.
    retrieval_rerank_enabled: bool = True
    # R-3: how many RRF candidates enter the cross-encoder before cutting to
    # top_k. Bigger = better recall, slower rerank.
    retrieval_rerank_candidates: int = 12

    # ── Langfuse observability (traces, evals, cost, latency) ────────────────
    # Leave the keys empty to run with observability disabled (graceful no-op —
    # the app behaves exactly as before). Keys: Langfuse project → Settings →
    # API Keys. Base URL: https://cloud.langfuse.com (EU), https://us.cloud.langfuse.com (US)
    langfuse_public_key: str = ''
    langfuse_secret_key: str = ''
    langfuse_base_url: str = 'https://cloud.langfuse.com'
    langfuse_enabled: bool = True
    # Trace sampling: 1.0 = trace every request. Lower for high-traffic phases.
    langfuse_sample_rate: float = 1.0
    # Bulk ingestion produces hundreds of spans per document — default to 10%.
    langfuse_ingest_sample_rate: float = 0.1
    # LLM-as-judge evals add a second Gemini call per evaluated item — sample
    # aggressively to control eval cost.
    langfuse_evals_sample_rate: float = 0.05


settings = Settings()

# ── Startup secret/misconfiguration guards ───────────────────────────────────
# A known-default SECRET_KEY makes JWTs forgeable (anyone can mint a token for
# any user id). Refuse to boot with one outside development; warn in dev.
_INSECURE_SECRET_VALUES = {
    '',
    'change-me-super-secret',
    'change-me',
    'replace-me',
    'changeme',
    'secret',
    'insecure',
}
_env = settings.app_env.strip().lower()
_is_dev_env = _env in ('development', 'dev', 'local', 'test', 'testing')
if settings.secret_key.strip().lower() in _INSECURE_SECRET_VALUES:
    if _is_dev_env:
        logging.getLogger(__name__).warning(
            'Using a default SECRET_KEY — development only. Set a strong random '
            'SECRET_KEY before deploying.'
        )
    else:
        raise RuntimeError(
            'Refusing to start: SECRET_KEY is a known default/insecure value. '
            'Set a strong random SECRET_KEY environment variable.'
        )
if _env == 'production' and settings.app_debug:
    logging.getLogger(__name__).warning(
        'APP_DEBUG=true in production exposes verbose error output — set APP_DEBUG=false.'
    )
