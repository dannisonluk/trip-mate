"""Rate limiting (§3.2, technical debt #3).

HTTP endpoints use `slowapi`; sensitive endpoints — login, registration, OTP —
are capped at 5 requests/minute per client. WebSocket messages use a separate
token-bucket (`services.kv.DistributedTokenBucket`, driven from `ws/manager.py`)
enforcing 2 messages/second per user.

**Why this file owns the counter instead of using slowapi's storage URI**
----------------------------------------------------------------------
slowapi can already talk to Redis (`storage_uri="redis://..."`), and for a
healthy Redis it is fine. The defect is what happens when Redis is *not* healthy:
`_pick_storage_uri` deliberately degrades to `memory://` so that a Redis blip
never turns into 429s on every login. But `memory://` counters are **per
process**, so during that window a limit of "5/minute" silently becomes
"5/minute per replica" — the quota multiplies by the replica count exactly when
the system is already degraded. The same defect, in the same shape, exists in the
WebSocket message bucket. Both are per-user quotas; both were per-replica
counters, so both are fixed here (the bucket half in `services/kv`).

**The sync/async constraint, stated plainly.** `slowapi` evaluates limits
*synchronously* inside the async request wrapper (`Limiter.__evaluate_limits`
calls `storage.incr`/`storage.get` from sync code), so the counter cannot await
the async client. This module therefore uses the **synchronous** `redis` client —
the `redis` package ships both, and the sync one can be called from inside a
running loop without thread tricks. Each counter read/write is a sub-millisecond
local call, which is why blocking the loop for it is acceptable; anything slower
would not be.

**Resilience contract**: availability of login beats strict enforcement.
1. A cheap TCP probe picks the in-memory counter when Redis is clearly absent
   (typical local dev), avoiding per-request connection attempts.
2. Every counter operation is wrapped and, on failure, degrades to the
   in-process counter rather than raising. `_resilient_hit` additionally
   guarantees the request is *allowed* if anything unexpected escapes.
"""
from __future__ import annotations

import logging
import socket
import time
from urllib.parse import urlparse

from app.core.config import settings
from app.services import kv

logger = logging.getLogger("tripmate.ratelimit")

# Same discipline as `services/kv.py`: a local, low-latency dependency that
# either answers fast or is not going to.
_CONNECT_TIMEOUT = 0.5
_SOCKET_TIMEOUT = 1.0
_COOLDOWN_SECONDS = 30.0

# Prefix so one Redis instance can host several environments, and so these keys
# are distinguishable from `tripmate:*` keys owned by other services.
_KEY_PREFIX = "tripmate:ratelimit:"


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


class _InProcessCounter:
    """Fallback counter: a fixed window per key, correct within one process.

    Used when Redis is absent, and on any Redis failure. Deliberately simple —
    it exists so that a Redis problem degrades the *scope* of the limit (per
    replica instead of global) rather than its *existence*.
    """

    def __init__(self) -> None:
        self._windows: dict[str, tuple[float, int]] = {}

    def incr(self, key: str, expiry: int) -> int:
        now = time.monotonic()
        window_start, count = self._windows.get(key, (now, 0))
        if now - window_start >= expiry:
            window_start, count = now, 0
        count += 1
        self._windows[key] = (window_start, count)
        # Opportunistic GC: without it the dict grows one entry per client
        # forever, which is a slow memory leak on a long-lived worker.
        if len(self._windows) > 10_000:
            cutoff = now - 3600
            self._windows = {k: v for k, v in self._windows.items() if v[0] > cutoff}
        return count

    def get(self, key: str) -> int:
        item = self._windows.get(key)
        return item[1] if item else 0

    def clear(self) -> None:
        self._windows.clear()


_process_counter = _InProcessCounter()

_redis_client = None

# The breaker window is shared with `services.kv` through the same pluggable
# backend (#22a): both modules talk to the same Redis, so one outage is one
# outage. Kept per-process by default, which is what keeps this working when
# Redis is unreachable — see `kv._BreakerState`.
_breaker = kv._BreakerState()


def _breaker_open() -> bool:
    return _breaker.is_open()


def _trip_breaker(exc: Exception) -> None:
    first = _breaker.trip()
    if first:
        logger.warning(
            "Redis unavailable for rate limiting (%s: %s) — limits are now "
            "enforced per replica for %ss. The effective quota is multiplied by "
            "the number of replicas during this window.",
            type(exc).__name__,
            exc,
            int(_COOLDOWN_SECONDS),
        )


def _redis():
    """Lazily build the *synchronous* client; None when unusable."""
    global _redis_client
    if _breaker.is_disabled() or _breaker.is_open():
        return None
    if _redis_client is not None:
        return _redis_client
    if not settings.RATE_LIMIT_ENABLED:
        return None
    url = settings.REDIS_URL
    if not url.startswith(("redis://", "rediss://", "unix://")):
        # e.g. memory:// — nothing to construct.
        _breaker.disable()
        return None
    try:
        import redis as redis_sync

        _redis_client = redis_sync.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=_CONNECT_TIMEOUT,
            socket_timeout=_SOCKET_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 — missing package or malformed URL
        logger.warning("Rate-limit Redis client cannot be created (%s) — per-process limits.", exc)
        _breaker.disable()
        _redis_client = None
    return _redis_client


class SharedCounter:
    """Fixed-window counter that is global across replicas when Redis is up.

    A *fixed* window rather than a sliding one on purpose: `INCR` + `EXPIRE` is
    the only counter operation that is atomic on a plain Redis key without a Lua
    script, and the boundary burst a fixed window permits (up to 2× the limit
    across a window edge) is irrelevant for abuse protection — the thing being
    defended against is sustained hammering, not a precisely metered rate. The
    alternative costs either a Lua script or a sliding-window key layout, both of
    which are more machinery than this guard needs.
    """

    def __init__(self, fallback: _InProcessCounter) -> None:
        self._fallback = fallback

    def incr(self, key: str, expiry: int, elastic_expiry: bool = False, amount: int = 1) -> int:
        full_key = f"{_KEY_PREFIX}{key}"
        # `_redis()` is called *inside* the guard: obtaining the client can fail
        # too (client construction, a bad URL, a DNS failure), and a raise here
        # would escape the fallback entirely and propagate into the request.
        # Guarding only the operation is the common half-fix.
        try:
            client = _redis()
            if client is None:
                return self._fallback.incr(key, expiry)
            pipe = client.pipeline()
            pipe.incrby(full_key, amount)
            pipe.expire(full_key, int(expiry))
            count, _ = pipe.execute()
            return int(count)
        except Exception as exc:  # noqa: BLE001 — degrade, never raise
            _trip_breaker(exc)
            return self._fallback.incr(key, expiry)

    def get(self, key: str) -> int:
        try:
            client = _redis()
            if client is None:
                return self._fallback.get(key)
            raw = client.get(f"{_KEY_PREFIX}{key}")
            return int(raw) if raw is not None else 0
        except Exception as exc:  # noqa: BLE001
            _trip_breaker(exc)
            return self._fallback.get(key)

    def get_expiry(self, key: str) -> float:
        try:
            client = _redis()
            if client is not None:
                ttl = client.ttl(f"{_KEY_PREFIX}{key}")
                if ttl and ttl > 0:
                    return time.time() + int(ttl)
        except Exception as exc:  # noqa: BLE001
            _trip_breaker(exc)
        return time.time()

    def reset(self) -> int:  # pragma: no cover — required by the storage protocol
        self._fallback.clear()
        return 0

    def check(self) -> None:  # pragma: no cover — interface completeness
        return None

    def clear(self, key: str | None = None) -> None:  # pragma: no cover
        self._fallback.clear()


class DistributedMovingWindow:
    """Drop-in replacement for `limits`' strategy, counting through `SharedCounter`.

    Implements just the `hit` contract that `Limiter.__evaluate_limits` uses —
    `hit(item, *identifiers, cost=N) -> bool` — so the accounting is ours while
    parsing the policy grammar, attaching the decorator, raising
    `RateLimitExceeded` and emitting the `X-RateLimit-*` headers all stay with
    slowapi. Replacing the whole extension would mean reimplementing the parts
    that are already correct.
    """

    def __init__(self, storage: SharedCounter) -> None:
        self.storage = storage

    def hit(self, item, *identifiers: str, cost: int = 1) -> bool:
        key = f"{item}:{':'.join(identifiers)}"
        # `item` is a `limits.RateLimitItem`; its `get_expiry()` is seconds.
        expiry = int(item.get_expiry())
        amount = max(1, int(cost))
        count = self.storage.incr(key, expiry, amount=amount)
        # The window started at the first hit, so the remaining allowance is
        # `limit - count`, and the request is admitted while count <= limit.
        return count <= item.amount


def reset_memory_store() -> None:
    """Test helper — clears the in-process fallback and re-arms the breaker."""
    _process_counter.clear()
    _breaker.rearm()


def set_breaker_backend(backend) -> None:
    """Share the breaker window across replicas (#22a) — see `services.kv`."""
    _breaker._shared = backend


try:  # slowapi is optional at import time so the app still boots without it.
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri="memory://",   # superseded below; kept so slowapi boots
        enabled=settings.RATE_LIMIT_ENABLED,
    )

    # Swap in the distributed strategy. Guarded because the attribute name is
    # private: if a slowapi upgrade moves it, we keep slowapi's own behaviour
    # (per-process limits) and say so, rather than failing to start.
    try:
        limiter._limiter = DistributedMovingWindow(SharedCounter(_process_counter))
    except Exception as exc:  # noqa: BLE001 — pragma: no cover
        logger.warning(
            "Could not install the shared rate-limit counter (%s) — limits are "
            "enforced per replica.",
            exc,
        )

    # --- Guard: never let a storage error break the request ----------------
    _strategy = limiter._limiter
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
# A healthy client refreshes at most once per access-token lifetime, so this is
# already ~2 orders of magnitude above normal use. It exists so that an
# attacker holding a stolen refresh cookie cannot burn through the rotation log
# at network speed — and, because a replayed token bumps the victim's epoch, a
# high rate here is also the signature of that attack.
REFRESH_RATE = "30/minute"
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
