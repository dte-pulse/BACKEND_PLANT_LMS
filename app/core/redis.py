import redis
from app.core.config import settings

redis_kwargs = {"decode_responses": True}
if settings.redis_url.startswith("rediss://"):
    redis_kwargs["ssl_cert_reqs"] = "none"

redis_client = redis.from_url(settings.redis_url, **redis_kwargs)
