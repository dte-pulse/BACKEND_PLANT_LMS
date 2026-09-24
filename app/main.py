from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.utils.bootstrap import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Langfuse: initialise the singleton at startup so the first traced request
    # doesn't pay init latency; flush + shutdown on exit so no traces are lost.
    from app.clients.langfuse_client import flush_langfuse, get_langfuse, shutdown_langfuse
    get_langfuse()
    yield
    flush_langfuse()
    shutdown_langfuse()


app = FastAPI(title=settings.app_name, debug=settings.app_debug, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.middleware('http')
async def security_headers(request: Request, call_next):
    """Baseline security headers on every response (VULN-011)."""
    response = await call_next(request)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'no-referrer')
    # The SPA is served separately; this API only needs to lock down its own docs.
    response.headers.setdefault(
        'Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'"
    )
    response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
    return response


app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get('/')
def root():
    return {'message': 'Pulse LMS API is running'}
