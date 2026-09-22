"""Rate limiting (§3.2).

HTTP endpoints use `slowapi`, backed by Redis when it is reachable and by
in-memory storage otherwise. Sensitive endpoints — login, registration, OTP —
are capped at 5 requests/minute per client. WebSocket messages use a separate
token-bucket (see `app.ws.manager`) enforcing 2 messages/second per user.

**Resilience contract**: the rate limiter must never be the reason a request
fails. `limits`' Redis storage raises `redis.exceptions.ConnectionError` when
the server is down, which slowapi does *not* catch — that would turn a Redis
blip into a 500 on every login/register/OTP call. Two guards prevent this:

1. A cheap TCP probe at import time picks in-memory storage when Redis is
   clearly absent (typical local dev), avoiding per-request connection attempts.
2. A wrapper around the strategy's `hit()` degrades to "allow" (fail-open) if
   the storage still errors at request time, e.g. Redis dies mid-flight.

Fail-open is the deliberate choice here: availability of login beats strict
rate-limit enforcement during an infrastructure outage.
"""
from __future__ import annotations

import logging
import socket
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger("tripmate.ratelimit")


def _redis_reachable(url: str, timeout: float = 0.4) -> bool:
    """Cheap TCP probe so we do not pay a full client handshake at import."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _pick_storage_uri() -> str:
    if not settings.RATE_LIMIT_ENABLED:
        return "memory://"
    url = settings.REDIS_URL
    # A non-Redis URI (e.g. memory://) is passed through untouched.
    if not url.startswith(("redis://", "rediss://", "unix://")):
        return url
    if _redis_reachable(url):
        return url
    logger.warning(
        "Redis at %s is unreachable — rate limiting falls back to in-memory "
        "storage. Limits will NOT be shared across processes.",
        url,
    )
    return "memory://"


try:  # slowapi is optional at import time so the app still boots without it.
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri=_pick_storage_uri(),
        enabled=settings.RATE_LIMIT_ENABLED,
    )

    # --- Guard 2: never let a storage error break the request --------------
    _strategy = limiter._limiter  # limits.strategies.*RateLimiter
    _original_hit = _strategy.hit

    def _resilient_hit(item, *identifiers, cost: int = 1):  # type: ignore[no-untyped-def]
        try:
            return _original_hit(item, *identifiers, cost=cost)
        except Exception as exc:  # noqa: BLE001 — any storage failure
            logger.warning(
                "Rate-limit storage error (%s: %s) — allowing request.",
                type(exc).__name__,
                exc,
            )
            return True  # fail-open

    _strategy.hit = _resilient_hit  # type: ignore[method-assign]

    SLOWAPI_AVAILABLE = True
except Exception:  # noqa: BLE001 — pragma: no cover
    limiter = None  # type: ignore[assignment]
    SLOWAPI_AVAILABLE = False

    def get_remote_address(request) -> str:  # type: ignore[misc]
        return getattr(getattr(request, "client", None), "host", "unknown") or "unknown"


# Named policies reused across routers.
LOGIN_RATE = "5/minute"
REGISTER_RATE = "5/minute"
OTP_RATE = "5/minute"
WRITE_RATE = "60/minute"
READ_RATE = "240/minute"
UPLOAD_RATE = "20/minute"


def limit(policy: str):
    """Apply a slowapi limit when available, otherwise a pass-through decorator."""

    def _decorator(func):
        if SLOWAPI_AVAILABLE and limiter is not None:
            return limiter.limit(policy)(func)
        return func

    return _decorator
