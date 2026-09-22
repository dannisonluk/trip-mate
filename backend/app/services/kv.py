"""Small async key-value store: Redis when reachable, in-process TTL dict otherwise.

Used for ephemeral state that must never touch the database — OTP codes and
revoked JWT ids. Both callers need the same semantics, so the Redis client and
the fallback live here rather than being duplicated per service.

**Why the circuit breaker matters.** A naive "try Redis, fall back on error"
implementation is not enough: when Redis is down, *every* call pays the full
connection-failure cost before falling back. On Windows that cost is ~4 seconds,
because `localhost` resolves to both `::1` and `127.0.0.1` and the unreachable
IPv6 attempt has to time out before IPv4 is tried. With a KV lookup on the login
path, that turned every request into a 4-second stall.

So we do three things:

1. Explicit short socket timeouts, so a single attempt cannot hang.
2. A circuit breaker: after one failure, skip Redis entirely for a cooldown
   window and go straight to memory. Only the first call after an outage pays.
3. Lazy client construction, so importing this module never touches the network.

The in-memory backend is per-process: correct for local dev and tests, but with
multiple replicas each keeps its own copy. State that must be shared across
replicas (OTP codes, revocation) therefore needs Redis in production — the
fallback is a convenience, not a substitute.
"""
from __future__ import annotations

import logging
import time

from app.core.config import settings

logger = logging.getLogger("tripmate.kv")

# Bound a single connection attempt. Redis is a local, low-latency dependency;
# if it cannot answer in this window it is not going to.
_CONNECT_TIMEOUT = 0.5
_SOCKET_TIMEOUT = 1.0

# How long to stay on the memory backend after a failure before retrying Redis.
_COOLDOWN_SECONDS = 30.0


class _MemoryStore:
    """Minimal TTL dict used when Redis is unavailable (dev / tests)."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}

    def set(self, key: str, value: str, ttl: int) -> None:
        self._data[key] = (value, time.monotonic() + ttl)

    def get(self, key: str) -> str | None:
        item = self._data.get(key)
        if not item:
            return None
        value, expires = item
        if time.monotonic() > expires:
            self._data.pop(key, None)
            return None
        return value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def incr(self, key: str, ttl: int) -> int:
        current = self.get(key)
        value = (int(current) if current is not None else 0) + 1
        self.set(key, str(value), ttl)
        return value

    def clear(self) -> None:
        self._data.clear()


_memory = _MemoryStore()
_redis_client = None
_redis_disabled = False        # permanently unusable (bad URL / package missing)
_cooldown_until = 0.0          # temporarily skipping Redis after a failure


def _breaker_open() -> bool:
    return time.monotonic() < _cooldown_until


def _trip_breaker(exc: Exception) -> None:
    """Stop hammering Redis for a while after a failure."""
    global _cooldown_until
    first = not _breaker_open()
    _cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
    if first:
        logger.warning(
            "Redis unavailable (%s: %s) — using the in-memory store for %ss.",
            type(exc).__name__,
            exc,
            int(_COOLDOWN_SECONDS),
        )


def _redis():
    """Lazily construct an async Redis client; returns None if unusable."""
    global _redis_client, _redis_disabled
    if _redis_disabled or _breaker_open():
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        from redis.asyncio import Redis

        _redis_client = Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=_CONNECT_TIMEOUT,
            socket_timeout=_SOCKET_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 — missing package or malformed URL
        logger.warning("Redis client cannot be created (%s) — in-memory store only.", exc)
        _redis_disabled = True
        _redis_client = None
    return _redis_client


async def set_value(key: str, value: str, ttl: int) -> None:
    client = _redis()
    if client is not None:
        try:
            await client.set(key, value, ex=ttl)
            return
        except Exception as exc:  # noqa: BLE001 — degrade to memory
            _trip_breaker(exc)
    _memory.set(key, value, ttl)


async def get_value(key: str) -> str | None:
    client = _redis()
    if client is not None:
        try:
            value = await client.get(key)
            if value is not None:
                return value
        except Exception as exc:  # noqa: BLE001
            _trip_breaker(exc)
    return _memory.get(key)


async def delete_value(key: str) -> None:
    client = _redis()
    if client is not None:
        try:
            await client.delete(key)
        except Exception as exc:  # noqa: BLE001
            _trip_breaker(exc)
    _memory.delete(key)


async def increment(key: str, ttl: int) -> int:
    """Atomically increment a counter, creating it with `ttl` if absent."""
    client = _redis()
    if client is not None:
        try:
            value = await client.incr(key)
            if value == 1:
                await client.expire(key, ttl)
            return int(value)
        except Exception as exc:  # noqa: BLE001
            _trip_breaker(exc)
    return _memory.incr(key, ttl)


def reset_memory_store() -> None:
    """Test helper — clears the in-process fallback and re-arms the breaker."""
    global _cooldown_until
    _memory.clear()
    _cooldown_until = 0.0
