"""Login rate limiting (VULN-006).

Redis-backed sliding counters:
- Per-employee-code failed attempts → account lockout with backoff.
- Per-IP failed attempts → blocks distributed guessing across many accounts.

Fail-open on Redis outage? No — fail-OPEN for availability but the audit log
still records the attempt (matching the project's graceful-degradation pattern
in response_cache.py). Brute force protection is best-effort when Redis is the
session store anyway; a full fail-closed variant can be enabled by flipping
FAIL_CLOSED to True.
"""
from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

# Tuning constants
MAX_ATTEMPTS_PER_ACCOUNT = 5          # failed attempts before account lockout
ACCOUNT_LOCKOUT_SECONDS = 15 * 60     # 15 minutes
MAX_ATTEMPTS_PER_IP = 30              # failed attempts per window per IP
IP_WINDOW_SECONDS = 15 * 60
FAIL_CLOSED = False                   # if True, Redis outage blocks all logins


class LoginRateLimited(Exception):
    """Raised when a login attempt exceeds the allowed failure budget."""

    def __init__(self, retry_after_seconds: int, reason: str):
        self.retry_after_seconds = retry_after_seconds
        self.reason = reason
        super().__init__(reason)


def _client():
    from app.core.redis import redis_client
    return redis_client


def check_login_allowed(employee_code: str, ip_address: str | None) -> None:
    """Raise LoginRateLimited if this account/IP has exhausted its attempt budget."""
    try:
        r = _client()
        code = (employee_code or 'unknown').strip().lower()

        account_key = f'ratelimit:login:acct:{code}'
        account_failures = int(r.get(account_key) or 0)
        if account_failures >= MAX_ATTEMPTS_PER_ACCOUNT:
            ttl = int(r.ttl(account_key) or ACCOUNT_LOCKOUT_SECONDS)
            raise LoginRateLimited(
                max(ttl, 1),
                f'Too many failed login attempts. Account locked for {max(ttl // 60, 1)} more minutes.'
            )

        if ip_address:
            ip_key = f'ratelimit:login:ip:{ip_address}'
            ip_failures = int(r.get(ip_key) or 0)
            if ip_failures >= MAX_ATTEMPTS_PER_IP:
                ttl = int(r.ttl(ip_key) or IP_WINDOW_SECONDS)
                raise LoginRateLimited(
                    max(ttl, 1),
                    'Too many failed login attempts from this network. Try again later.'
                )
    except LoginRateLimited:
        raise
    except Exception as e:  # Redis unavailable
        if FAIL_CLOSED:
            raise LoginRateLimited(60, 'Login temporarily unavailable. Try again shortly.')
        logger.warning(f'Login rate limiter unavailable (fail-open): {e}')


def record_failed_login(employee_code: str, ip_address: str | None) -> None:
    """Increment failure counters after a failed authentication attempt."""
    try:
        r = _client()
        code = (employee_code or 'unknown').strip().lower()

        account_key = f'ratelimit:login:acct:{code}'
        pipe = r.pipeline()
        pipe.incr(account_key)
        pipe.expire(account_key, ACCOUNT_LOCKOUT_SECONDS)
        pipe.execute()

        if ip_address:
            ip_key = f'ratelimit:login:ip:{ip_address}'
            pipe = r.pipeline()
            pipe.incr(ip_key)
            pipe.expire(ip_key, IP_WINDOW_SECONDS)
            pipe.execute()
    except Exception as e:
        logger.warning(f'Failed to record login failure for rate limiting: {e}')


def clear_failed_logins(employee_code: str) -> None:
    """Reset the account counter on successful login."""
    try:
        r = _client()
        r.delete(f'ratelimit:login:acct:{(employee_code or "").strip().lower()}')
    except Exception as e:
        logger.warning(f'Failed to clear login rate-limit counters: {e}')
