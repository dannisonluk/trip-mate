"""Per-user quotas must be shared across replicas (technical debt #3 + #6 remnant).

The defect being guarded
------------------------
Both of these limits used to live in per-process state:

* WebSocket messages — 2 msg/s per user, counted in a dict on the
  `ConnectionManager` instance. With `uvicorn --workers 4` there are four
  instances, so a user whose tabs land on different workers gets 2 msg/s *each*.
* HTTP requests — slowapi's storage degrades to `memory://` when Redis is
  unreachable, which is per process, so "5/minute" silently becomes
  "5/minute per replica" during a Redis outage.

Neither failure raises an error. The limit is simply larger than the operator
believes it to be, and *how much* larger depends on the replica count and on
which worker the load balancer picked.

Modeling a replica
------------------
The subtle part, and the reason the first version of these tests was worthless:
**two `ConnectionManager` objects in one process are not two replicas.** They
share the module-level state in `kv`, so a manager-per-instance dict would still
look "shared" and the test would pass without proving anything. That is exactly
what the first mutation run showed — bypassing Redis entirely left every test
green.

So a replica here is a **fresh `kv` module**, built with `importlib` from the
same source file, each pointed at its own store. That reproduces what
`--workers N` gives you: N Python processes, each with its own module globals,
sharing only Redis. It is a heavier fixture, and it is the only arrangement in
which "the quota is shared" is a real claim rather than a restatement of "these
two objects are in the same process".

What is and is not covered
--------------------------
Redis is not available here, so the shared store is faked. The fake models the
one property under test — a counter keyed by name that persists across callers.
It does **not** prove anything about a live Redis: key expiry under load, clock
skew between replicas, and real failover behaviour belong in an integration job.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path

import pytest

from app.core import rate_limit
from app.services import kv
from app.ws.manager import ConnectionManager

_KV_PATH = Path(kv.__file__)


class FakeSharedRedis:
    """A store that is *shared* between everything that holds a reference.

    The property under test is that two independent callers reading and writing
    the same key see each other. A per-instance dict would reproduce the bug and
    make every test below pass vacuously, so the dict lives on the fake and
    multiple clients are deliberately created over the same fake.
    """

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.expiry: dict[str, float] = {}
        self.set_calls = 0

    def _live(self, key: str) -> bool:
        exp = self.expiry.get(key)
        if exp is not None and time.monotonic() > exp:
            self.data.pop(key, None)
            self.expiry.pop(key, None)
            return False
        return True

    async def get(self, key: str):
        if not self._live(key):
            return None
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.set_calls += 1
        self.data[key] = value
        if ex is not None:
            self.expiry[key] = time.monotonic() + ex

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)

    async def incr(self, key: str) -> int:
        current = self.data.get(key)
        value = (int(current) if current is not None else 0) + 1
        self.data[key] = str(value)
        return value

    async def expire(self, key: str, ttl: int) -> None:
        self.expiry[key] = time.monotonic() + ttl

    async def aclose(self) -> None:
        pass


def _fresh_kv_module(name: str, redis):
    """Load an independent copy of `services.kv`, bound to `redis`.

    Each call produces a module with its **own** globals (`_memory`,
    `_redis_client`, `_breaker`), which is what a separate worker process
    has. Returning the same module for both replicas would silently make the
    per-replica bug untestable — see the module docstring.
    """
    spec = importlib.util.spec_from_file_location(name, _KV_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    module._redis = lambda: redis          # type: ignore[attr-defined]
    module.reset_breaker_for_tests()       # type: ignore[attr-defined]
    return module


@pytest.fixture()
def shared(monkeypatch):
    """Point the *app's* `kv` at a shared fake and re-arm its breaker.

    Also re-arms `rate_limit`'s breaker: `test_http_counter_is_per_client` calls
    `_breaker.disable()`, which is a *permanent* disable, so without this the
    breaker test that follows would pass for the wrong reason (disabled, not
    tripped) — and would keep passing with the breaker removed entirely.
    """
    fake = FakeSharedRedis()
    monkeypatch.setattr(kv, "_redis", lambda: fake)
    kv.reset_breaker_for_tests()
    kv.reset_memory_store()
    rate_limit.reset_memory_store()
    rate_limit._breaker.rearm()
    rate_limit._breaker.disabled = False
    return fake


def _manager_for(kv_module, *, rate: float = 2.0) -> ConnectionManager:
    """A manager whose bucket reads through `kv_module`.

    The bucket rate and the manager's declared rate are the same value by
    construction — passing them separately would let a test drive a 40/s bucket
    while the key namespace still said 2/s, which is precisely the kind of
    mismatch that makes a passing test meaningless.
    """
    bucket = kv_module.DistributedTokenBucket(rate=rate, capacity=rate)
    return ConnectionManager(msg_rate_per_second=rate, bucket=bucket)


# --- WebSocket message quota ------------------------------------------------


@pytest.mark.asyncio
async def test_message_quota_is_shared_between_replicas():
    """Two replicas must share one bucket for the same user.

    This is the #6 remnant. The counting used to happen in a dict on the manager,
    so a second replica started with a full bucket and granted another 2 msg/s.
    The two managers here are backed by **independent `kv` modules** — i.e. two
    processes — sharing only the fake Redis, so "the total is capped" means the
    cap is genuinely global.
    """
    redis = FakeSharedRedis()
    kv_a = _fresh_kv_module("kv_replica_a", redis)
    kv_b = _fresh_kv_module("kv_replica_b", redis)

    replica_a = _manager_for(kv_a)
    replica_b = _manager_for(kv_b)

    allowed_a = [await replica_a.allow_message("profile-1") for _ in range(2)]
    allowed_b = [await replica_b.allow_message("profile-1") for _ in range(2)]

    assert allowed_a == [True, True]
    assert allowed_b == [False, False], (
        "a second replica granted more messages for the same user — the quota "
        "is still counted per replica, so the effective limit is N x 2 msg/s"
    )


@pytest.mark.asyncio
async def test_message_quota_is_per_user():
    """Sharing must not turn the limit into one global budget.

    The failure mode of an over-eager fix: keying the bucket by something
    replica-wide (or forgetting the profile id entirely) makes one busy user
    throttle everybody. Asserted by spending user 1's budget and checking user 2
    is unaffected.
    """
    redis = FakeSharedRedis()
    kv_a = _fresh_kv_module("kv_user_a", redis)
    manager = _manager_for(kv_a)

    for _ in range(3):
        await manager.allow_message("profile-1")

    assert await manager.allow_message("profile-2") is True, (
        "user 2 was throttled by user 1's spend — the bucket is not keyed per user"
    )


@pytest.mark.asyncio
async def test_message_quota_refills():
    """The bucket must still refill — a shared counter that never resets would
    throttle a user permanently after the burst is spent.

    Capacity equals the rate in this design (`2/s` = 2 tokens, refilling at
    2/s), so the test drains the burst and then waits half a second for one
    token. A rate of 4/s keeps the wait at 0.3s while still leaving the bucket
    empty after the burst.
    """
    redis = FakeSharedRedis()
    kv_a = _fresh_kv_module("kv_refill", redis)
    manager = _manager_for(kv_a, rate=4.0)

    # Drain the 4-token burst.
    for _ in range(4):
        assert await manager.allow_message("profile-1") is True
    assert await manager.allow_message("profile-1") is False, "burst of 4 was not the capacity"

    await asyncio.sleep(0.3)   # ~1.2 tokens at 4/s
    assert await manager.allow_message("profile-1") is True, "the bucket never refilled"


@pytest.mark.asyncio
async def test_message_quota_falls_back_when_redis_is_gone():
    """Redis down must degrade the *scope* of the limit, not remove it.

    Fail-open here would mean a flood has no ceiling at all during an outage —
    strictly worse than a per-replica limit. The assertion is that the limit
    still exists (a third message is refused).
    """
    def boom():
        raise RuntimeError("redis gone")

    kv_a = _fresh_kv_module("kv_down", FakeSharedRedis())
    kv_a._redis = boom                     # type: ignore[attr-defined]
    manager = _manager_for(kv_a)

    allowed = [await manager.allow_message("profile-1") for _ in range(3)]

    assert allowed == [True, True, False], (
        "with Redis unavailable the message limit disappeared entirely instead of "
        "degrading to a per-process bucket"
    )


# --- HTTP request quota -----------------------------------------------------


class _FakeItem:
    """Stands in for `limits.RateLimitItem`."""

    def __init__(self, amount: int, expiry: int) -> None:
        self.amount = amount
        self._expiry = expiry

    def get_expiry(self) -> int:
        return self._expiry


class _SyncPipeline:
    def __init__(self, fake: FakeSharedRedis) -> None:
        self._fake = fake
        self._key: str | None = None

    def incrby(self, key: str, amount: int):
        self._fake.data[key] = str(int(self._fake.data.get(key, "0")) + amount)
        self._key = key
        return self

    def expire(self, key: str, ttl: int):
        self._fake.expiry[key] = time.monotonic() + ttl
        return self

    def execute(self):
        return int(self._fake.data[self._key]), True


class _SyncClient:
    """The synchronous client `rate_limit` uses, over the shared fake."""

    def __init__(self, fake: FakeSharedRedis) -> None:
        self._fake = fake

    def pipeline(self):
        return _SyncPipeline(self._fake)

    def get(self, key: str):
        return self._fake.data.get(key)

    def ttl(self, key: str) -> int:
        return 60


@pytest.fixture()
def http_counter(shared, monkeypatch):
    """A `SharedCounter` over the shared fake, wired to the sync client."""
    monkeypatch.setattr(rate_limit, "_redis", lambda: _SyncClient(shared))
    rate_limit.reset_memory_store()
    return rate_limit.SharedCounter(rate_limit._InProcessCounter())


def test_http_counter_is_shared(shared, monkeypatch):
    """The HTTP counter must be shared, which is what `memory://` could not be.

    Two replicas crediting the same key must see a single count. This is why the
    two counters get **separate** fallbacks: reusing one `SharedCounter` (or one
    fallback store) would share the count through the fallback and the test would
    pass even with Redis bypassed — the exact false-pass the first version of
    this test produced.
    """
    monkeypatch.setattr(rate_limit, "_redis", lambda: _SyncClient(shared))
    rate_limit.reset_memory_store()

    item = _FakeItem(amount=5, expiry=60)
    counter_a = rate_limit.SharedCounter(rate_limit._InProcessCounter())
    counter_b = rate_limit.SharedCounter(rate_limit._InProcessCounter())
    strategy_a = rate_limit.DistributedMovingWindow(counter_a)
    strategy_b = rate_limit.DistributedMovingWindow(counter_b)

    results = [strategy_a.hit(item, "client-1") for _ in range(3)]
    results += [strategy_b.hit(item, "client-1") for _ in range(4)]

    assert results == [True, True, True, True, True, False, False], (
        f"got {results} — a per-replica counter would allow 5 from each replica"
    )


def test_http_counter_is_per_client(shared, monkeypatch, http_counter):
    rate_limit._breaker.disable()  # force the fallback
    item = _FakeItem(amount=2, expiry=60)
    strategy = rate_limit.DistributedMovingWindow(http_counter)

    assert strategy.hit(item, "client-a") is True
    assert strategy.hit(item, "client-a") is True
    assert strategy.hit(item, "client-a") is False
    assert strategy.hit(item, "client-b") is True, (
        "one client's spend throttled another — the counter key is not per client"
    )


def test_http_counter_degrades_when_redis_fails(shared, http_counter):
    """A Redis failure must degrade the scope, not the existence, of the limit."""
    def boom():
        raise RuntimeError("redis gone")

    rate_limit._redis = boom  # noqa: SLF001 — patched per-test, undone by the fixture
    try:
        item = _FakeItem(amount=2, expiry=60)
        strategy = rate_limit.DistributedMovingWindow(http_counter)

        assert strategy.hit(item, "client-a") is True
        assert strategy.hit(item, "client-a") is True
        assert strategy.hit(item, "client-a") is False, (
            "the HTTP limit vanished when Redis failed instead of degrading per-process"
        )
    finally:
        rate_limit.reset_memory_store()


def test_http_counter_trips_the_breaker(shared, monkeypatch):
    """A failure must stop retrying Redis, or every request pays the timeout.

    Mirrors the `kv` behaviour, for the same reason: on Windows an unreachable
    localhost costs ~4 s per attempt, and this sits on the login path.

    The failure has to be a **runtime** one, not a construction one. If
    `from_url` fails, `_redis()` calls `_breaker.disable()` — which is a
    *permanent* disable, so retries stop for a reason that has nothing to do with
    the breaker and the test would pass with the breaker removed. That is exactly
    what the first version did. Here the client builds fine and its `pipeline()`
    raises, which is the case the breaker exists for: Redis was reachable when we
    connected and then went away.
    """
    calls = {"n": 0}

    class _BrokenPipeline:
        def incrby(self, *args, **kwargs):
            return self

        def expire(self, *args, **kwargs):
            return self

        def execute(self):
            raise RuntimeError("connection lost mid-flight")

    class _BrokenClient:
        def pipeline(self):
            # Counted here: this is "we went to Redis". With the breaker open it
            # must not be called at all.
            calls["n"] += 1
            return _BrokenPipeline()

        def get(self, key):
            raise RuntimeError("connection lost mid-flight")

        def ttl(self, key):
            return -1

    class _ClientClass:
        """Stands in for `redis.Redis`: construction succeeds, use fails."""

        @classmethod
        def from_url(cls, *args, **kwargs):
            return _BrokenClient()

    import redis as redis_sync

    # Patch the *class*, not `_redis`. The breaker lives inside `_redis`, so
    # replacing `_redis` itself would bypass the very thing under test — that
    # mistake made the previous version of this test report "20 retries".
    # `RATE_LIMIT_ENABLED` must be on because `conftest.py` disables it and
    # `_redis()` short-circuits when it is off.
    monkeypatch.setattr(rate_limit.settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(redis_sync, "Redis", _ClientClass)
    monkeypatch.setattr(rate_limit, "_redis_client", None)
    rate_limit.reset_memory_store()

    item = _FakeItem(amount=100, expiry=60)
    strategy = rate_limit.DistributedMovingWindow(rate_limit.SharedCounter(rate_limit._InProcessCounter()))

    start = time.monotonic()
    for _ in range(20):
        strategy.hit(item, "client-a")
    elapsed = time.monotonic() - start

    assert calls["n"] == 1, (
        f"the client was exercised {calls['n']} times after it started failing — "
        "the breaker is not short-circuiting Redis"
    )
    assert elapsed < 0.5, f"20 hits took {elapsed:.2f}s — the breaker is not short-circuiting"

