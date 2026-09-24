import logging
import time

from sqlalchemy import create_engine, event
from sqlalchemy.exc import DisconnectionError
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.database_url,
    future=True,
    # NOTE: pool_pre_ping is intentionally NOT enabled. It executes a remote-DB
    # round-trip on EVERY checkout (~400ms on cloud Postgres). Instead we ping
    # only connections that have been idle longer than db_idle_ping_seconds
    # (see the checkout listener below); hot/recently-used connections skip the
    # round-trip entirely.
    pool_size=5,
    max_overflow=10,
    pool_timeout=15,
    pool_recycle=300,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()

# Idle-ping threshold (seconds). Connections unused for at least this long are
# validated with SELECT 1 before use; more recently used ones are assumed alive.
#
# Accepted tradeoff: a pooled connection that dies while idle for LESS than
# IDLE_PING_SECONDS (server timeout, network blip, firewall kill) will surface
# as a raw OperationalError on the first query. SQLAlchemy's own disconnect
# retry usually recovers it on that first failed query, but at the cost of a
# failed round-trip rather than a cheap ping. This is the deliberate price of
# not paying a remote-DB round-trip on every checkout.
IDLE_PING_SECONDS = max(1, int(settings.db_idle_ping_seconds))
_LAST_USE_KEY = 'idle_ping_last_use'


@event.listens_for(engine, 'connect')
def _stamp_on_connect(dbapi_connection, connection_record):
    """Brand-new connection — mark as freshly used so it isn't pinged on its
    very first checkout (it was just created)."""
    connection_record.info[_LAST_USE_KEY] = time.monotonic()


@event.listens_for(engine, 'reset')
def _stamp_on_reset(dbapi_connection, connection_record):
    """Connection returned to the pool — start the idle clock from here."""
    connection_record.info[_LAST_USE_KEY] = time.monotonic()


@event.listens_for(engine, 'checkout')
def _ping_if_idle(dbapi_connection, connection_record, connection_proxy):
    """Ping a pooled connection ONLY when it has been idle past the threshold.

    Replaces pool_pre_ping, which paid a remote-DB round-trip on every single
    checkout. For a remote (Aiven cloud) Postgres that is ~400ms per request;
    with the response cache the per-request floor is dominated by this ping +
    the auth user lookup, so eliminating the unconditional ping is a real win.

    On ping failure we raise DisconnectionError so SQLAlchemy invalidates the
    stale connection and transparently retries with a fresh one — the same
    safety net pool_pre_ping provides, just scoped to idle connections.
    """
    now = time.monotonic()
    last_use = connection_record.info.get(_LAST_USE_KEY, 0.0)
    if now - last_use < IDLE_PING_SECONDS:
        return  # recently used — skip the round-trip
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute('SELECT 1')
        cursor.close()
    except Exception as e:  # noqa: BLE001
        logger.warning('Idle pooled DB connection failed ping — invalidating: %s', e)
        raise DisconnectionError('idle DB connection ping failed') from e


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
