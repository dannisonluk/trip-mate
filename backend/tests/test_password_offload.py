"""Argon2 must run off the event loop.

Argon2 is intentionally slow (~33 ms at our cost parameters). If it runs inline
in an `async def`, every concurrent request — including `/health` — waits behind
it, because the loop cannot schedule anything else while it burns CPU.

These tests pin that the offload exists. Without them the wrappers could be
replaced by direct sync calls during a refactor and nothing else would fail.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.core import security


def test_hash_runs_in_a_worker_thread():
    """The sync primitive must execute on a different thread than the loop."""
    captured: dict[str, int] = {}
    loop_thread = threading.get_ident()

    def probe(password: str) -> str:
        captured["tid"] = threading.get_ident()
        return "hashed"

    original = security.hash_password
    security.hash_password = probe  # type: ignore[assignment]
    try:
        result = asyncio.run(security.hash_password_async("Passw0rd123"))
    finally:
        security.hash_password = original  # type: ignore[assignment]

    assert result == "hashed"
    assert "tid" in captured, "the primitive was never called"
    assert captured["tid"] != loop_thread, "hash ran on the event loop thread"


def test_verify_runs_in_a_worker_thread():
    captured: dict[str, int] = {}
    loop_thread = threading.get_ident()

    def probe(password: str, hashed: str) -> bool:
        captured["tid"] = threading.get_ident()
        return True

    original = security.verify_password
    security.verify_password = probe  # type: ignore[assignment]
    try:
        assert asyncio.run(security.verify_password_async("pw", "hash")) is True
    finally:
        security.verify_password = original  # type: ignore[assignment]

    assert captured["tid"] != loop_thread


def test_event_loop_stays_responsive_while_hashing(monkeypatch):
    """While a hash is in flight, other coroutines must keep being scheduled.

    Uses a deliberately slow stand-in (300 ms) so the margin is enormous: a
    correctly offloaded hash lets the 5 ms ticker fire ~60 times, whereas an
    inline hash would let it fire ~0 times. That gap makes the assertion
    reliable even on a loaded CI box.
    """
    monkeypatch.setattr(security, "hash_password", lambda pw: time.sleep(0.3) or "h")
    monkeypatch.setattr(security, "verify_password", lambda pw, h: time.sleep(0.3) or True)

    async def scenario() -> int:
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.005)
                ticks += 1

        task = asyncio.create_task(ticker())
        try:
            await security.hash_password_async("Passw0rd123")
        finally:
            task.cancel()
        return ticks

    assert asyncio.run(scenario()) >= 5


def test_concurrent_hashes_do_not_serialise():
    """N concurrent hashes should finish in well under N x single-hash time.

    argon2 releases the GIL, so real parallelism is available. A generous 0.75x
    threshold tolerates a slow/oversubscribed machine while still failing loudly
    if the work ever moves back onto the single-threaded event loop.
    """
    password = "Passw0rd123"
    single = time.perf_counter()
    asyncio.run(security.hash_password_async(password))
    single = time.perf_counter() - single

    n = 4

    async def many() -> float:
        start = time.perf_counter()
        await asyncio.gather(*(security.hash_password_async(password) for _ in range(n)))
        return time.perf_counter() - start

    concurrent = asyncio.run(many())
    assert concurrent < single * n * 0.75, (
        f"{n} hashes took {concurrent:.2f}s vs {single:.2f}s for one — "
        "they appear to be serialising on the event loop"
    )


def test_thread_limiter_cap_is_configured():
    """The pool must be capped: 40 threads x 64 MiB could OOM a small container."""
    from app.core.config import settings

    assert settings.PASSWORD_HASH_MAX_CONCURRENCY <= 16, (
        "Argon2 memory_cost is 64 MiB per call; a large pool risks OOM"
    )


@pytest.mark.parametrize("value", [1, 8])
def test_limiter_accepts_configured_cap(value):
    """Sanity-check the cap is applicable.

    `current_default_thread_limiter()` needs a running loop, so this must be
    driven from inside one. A cap of 0 would deadlock every hash call.
    """
    from anyio import to_thread

    async def scenario() -> int:
        limiter = to_thread.current_default_thread_limiter()
        limiter.total_tokens = value
        return limiter.total_tokens

    assert asyncio.run(scenario()) == value
