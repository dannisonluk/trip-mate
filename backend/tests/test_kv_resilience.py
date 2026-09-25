"""The ephemeral KV store must degrade quietly when Redis is absent.

Regression guard for a real bug: "try Redis, fall back on error" is not enough
when Redis is down, because *every* call pays the connection-failure cost. On
Windows that cost is ~4s (an unreachable IPv6 `::1` attempt has to time out
before IPv4 is tried), and a KV lookup sits on the login/refresh path — so every
auth request stalled for seconds.

The circuit breaker makes only the first call pay.
"""
from __future__ import annotations

import asyncio
import time

from app.core.config import settings
from app.services import kv

# Port 1 is reserved and never listening, and 127.0.0.1 avoids the IPv6 detour,
# so a failure here is immediate and deterministic.
DEAD_REDIS = "redis://127.0.0.1:1/0"


def _reset(monkeypatch, url: str = DEAD_REDIS) -> None:
    monkeypatch.setattr(settings, "REDIS_URL", url)
    monkeypatch.setattr(kv, "_redis_client", None)
    # The breaker state used to be two module globals (`_redis_disabled`,
    # `_cooldown_until`); it is now a `_BreakerState` instance so the window can
    # be shared across replicas (#22a). Same semantics, one object — and the
    # test asserts through the public `_breaker_open()` rather than the field, so
    # a future move of the state does not silently stop being covered.
    kv.reset_breaker_for_tests()
    kv.reset_memory_store()


def test_kv_still_works_when_redis_is_down(monkeypatch):
    _reset(monkeypatch)

    async def run():
        await kv.set_value("k", "v", 60)
        assert await kv.get_value("k") == "v"
        assert await kv.increment("c", 60) == 1
        assert await kv.increment("c", 60) == 2
        await kv.delete_value("k")
        assert await kv.get_value("k") is None

    asyncio.run(run())


def test_breaker_opens_after_a_failure(monkeypatch):
    _reset(monkeypatch)
    assert not kv._breaker_open()

    asyncio.run(kv.set_value("k", "v", 60))

    assert kv._breaker_open(), "a failed call must open the breaker"
    assert kv._redis() is None, "the breaker must short-circuit Redis entirely"


def test_repeated_calls_do_not_retry_redis(monkeypatch):
    """Only the first call should pay the failure cost."""
    _reset(monkeypatch)

    async def run():
        start = time.monotonic()
        await kv.set_value("k", "v", 60)          # pays once
        first = time.monotonic() - start

        start = time.monotonic()
        for _ in range(50):
            assert await kv.get_value("k") == "v"
        rest = time.monotonic() - start
        return first, rest

    first, rest = asyncio.run(run())

    assert first < 2.0, f"first call stalled for {first:.2f}s"
    assert rest < 0.5, (
        f"50 calls after the breaker opened took {rest:.2f}s — "
        "the breaker is not short-circuiting Redis"
    )


def test_reset_re_arms_the_breaker(monkeypatch):
    _reset(monkeypatch)
    asyncio.run(kv.set_value("k", "v", 60))
    assert kv._breaker_open()

    kv.reset_memory_store()

    assert not kv._breaker_open()
    assert kv._redis() is not None


def test_memory_store_is_per_key_and_respects_ttl(monkeypatch):
    _reset(monkeypatch)

    async def run():
        await kv.set_value("short", "v", 1)
        assert await kv.get_value("short") == "v"
        # Rewrite the expiry directly rather than sleeping a full second.
        kv._memory.set("short", "v", 0)
        time.sleep(0.01)
        assert await kv.get_value("short") is None

    asyncio.run(run())
