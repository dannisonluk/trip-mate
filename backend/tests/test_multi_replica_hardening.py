"""Multi-replica hardening: #8, #21, #22a, #22b.

These four items share one root cause — **a limit that is enforced per process
but is meant to describe the whole deployment**. They were previously recorded as
"single-replica trade-offs", which was true and also the reason none of them had
a test: a per-process bound behaves identically to a global one until a second
replica exists.

The lesson from `tests/test_shared_quota.py` carries over verbatim and is the
single most important thing in this file:

    **Two objects in one process are NOT two replicas.**

Two `DistributedSemaphore`s constructed in the same test share module-level
`_memory`, so a per-replica fallback counter still looks shared, and a mutation
that removes the Redis call leaves every test green. A replica has to be modelled
as a *fresh copy of the module* (`_fresh_kv_module` below), with its own globals,
sharing only the fake store. Same for `rate_limit` and for the breaker.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path

import pytest

from app.core import rate_limit, security
from app.core.config import settings
from app.services import kv

_KV_PATH = Path(kv.__file__)
_RATE_LIMIT_PATH = Path(rate_limit.__file__)


# ---------------------------------------------------------------------------
# A shared fake Redis
# ---------------------------------------------------------------------------
class FakeSharedRedis:
    """The subset of the Redis API these tests exercise, shared across replicas.

    Deliberately *not* an asyncio-aware fake with per-caller state: the whole
    point is that every "replica" talks to the same store.
    """

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.expiry: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value
        if ex is not None:
            self.expiry[key] = ex

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)

    async def incr(self, key: str) -> int:
        value = int(self.data.get(key, 0)) + 1
        self.data[key] = str(value)
        return value

    async def decr(self, key: str) -> int:
        value = int(self.data.get(key, 0)) - 1
        self.data[key] = str(value)
        return value

    async def expire(self, key: str, ttl: int) -> None:
        self.expiry[key] = ttl

    async def aclose(self) -> None:
        pass


def _fresh_kv_module(name: str, redis=None):
    """Load an independent copy of `services.kv` — i.e. a second replica.

    Each call produces a module with its **own** `_memory`, `_redis_client` and
    `_breaker`, which is what a separate worker process has. Reusing the imported
    module for both replicas would share the fallback and make the per-replica
    bug untestable.
    """
    spec = importlib.util.spec_from_file_location(name, _KV_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    if redis is not None:
        module._redis = lambda: redis  # type: ignore[attr-defined]
    module.reset_breaker_for_tests()  # type: ignore[attr-defined]
    return module


def _fresh_rate_limit_module(name: str, redis=None):
    """Same trick for `core.rate_limit` — a replica of *that* module."""
    spec = importlib.util.spec_from_file_location(name, _RATE_LIMIT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    if redis is not None:
        module._redis = lambda: redis  # type: ignore[attr-defined]
    module._breaker.rearm()
    module._breaker.disabled = False
    return module


# ---------------------------------------------------------------------------
# #8 — the Argon2 concurrency cap must be global, not per process
# ---------------------------------------------------------------------------
def test_semaphore_admits_exactly_the_limit_across_replicas():
    """The cap is a deployment-wide number: two replicas cannot exceed it.

    This is the memory budget. 8 slots x 4 replicas of 64 MiB is the arithmetic
    the setting exists to prevent, so the assertion is on the *total* held, not
    on what any one replica did.
    """
    redis = FakeSharedRedis()
    replica_a = _fresh_kv_module("kv_a", redis)
    replica_b = _fresh_kv_module("kv_b", redis)

    sem_a = replica_a.DistributedSemaphore(limit=4)
    sem_b = replica_b.DistributedSemaphore(limit=4)

    async def run():
        # Each replica tries to take the whole cap; together only 4 permits exist.
        got_a = [await sem_a.acquire("slots") for _ in range(4)]
        got_b = [await sem_b.acquire("slots") for _ in range(4)]
        return got_a, got_b

    got_a, got_b = asyncio.run(run())

    assert got_a == [True, True, True, True], got_a
    assert got_b == [False, False, False, False], (
        f"replica B got {got_b} after replica A took all 4 permits — the cap is "
        "being counted per replica, so N replicas each get the full budget"
    )
    assert redis.data["slots"] == "4"


def test_semaphore_release_returns_a_permit_to_everyone():
    """Releasing must be visible to the *other* replica, not just the holder."""
    redis = FakeSharedRedis()
    a = _fresh_kv_module("kv_a", redis)
    b = _fresh_kv_module("kv_b", redis)
    sem_a = a.DistributedSemaphore(limit=1)
    sem_b = b.DistributedSemaphore(limit=1)

    async def run():
        assert await sem_a.acquire("k") is True
        assert await sem_b.acquire("k") is False, "the cap was per replica"
        await sem_a.release("k")
        return await sem_b.acquire("k")

    assert asyncio.run(run()) is True, "a released permit did not become available"


def test_semaphore_degrades_to_per_replica_when_redis_is_gone():
    """The failure direction is a *larger* cap, never a denied caller.

    With Redis unreachable the counter falls back to the per-process store. That
    over-runs the memory budget by the replica count — bad, but strictly better
    than the alternative failure mode (nobody can log in because the "shared"
    counter cannot be read), which is the trap the token bucket also avoids.
    """
    sem = kv.DistributedSemaphore(limit=2)
    kv.reset_memory_store()

    async def run():
        # `_redis()` is left to fail naturally (no local Redis in the suite).
        return [await sem.acquire("degrade") for _ in range(3)]

    assert asyncio.run(run()) == [True, True, False], (
        "the fallback must still enforce the cap within the process"
    )


def test_hash_wrappers_go_through_the_shared_semaphore(monkeypatch):
    """The cap must actually be taken by the hash helpers, not merely declared.

    Without this the semaphore could exist and be correct while every login
    bypassed it — the setting would look enforced and be decorative.
    """
    taken: list[str] = []

    class _SpySemaphore:
        def __init__(self, *, limit):
            taken.append(f"limit={limit}")

        async def acquire(self, key):
            taken.append(f"acquire:{key}")
            return True

        async def release(self, key):
            taken.append(f"release:{key}")

    monkeypatch.setattr(security.kv, "DistributedSemaphore", _SpySemaphore)
    monkeypatch.setattr(security, "_hash_slots", None)
    monkeypatch.setattr(security, "hash_password", lambda pw: "hashed")

    result = asyncio.run(security.hash_password_async("Passw0rd123"))

    assert result == "hashed"
    assert f"acquire:{security._HASH_SLOT_KEY}" in taken, taken
    assert f"release:{security._HASH_SLOT_KEY}" in taken, (
        f"the permit was never released — {taken}"
    )
    assert f"limit={settings.PASSWORD_HASH_MAX_CONCURRENCY}" in taken, (
        f"the semaphore was built with the wrong limit: {taken}"
    )


def test_hash_slot_is_released_when_the_hash_raises(monkeypatch):
    """A raise must not leak the permit.

    A stranded permit is only reclaimed when the Redis key's TTL lapses, so one
    leak per failed call would slowly strangle the pool — and the failure would
    look like "logins got slow" rather than "we leak permits".
    """
    events: list[str] = []

    class _SpySemaphore:
        def __init__(self, *, limit):
            pass

        async def acquire(self, key):
            events.append("acquire")
            return True

        async def release(self, key):
            events.append("release")

    def _boom(password: str) -> str:
        raise RuntimeError("argon2 exploded")

    monkeypatch.setattr(security.kv, "DistributedSemaphore", _SpySemaphore)
    monkeypatch.setattr(security, "_hash_slots", None)
    monkeypatch.setattr(security, "hash_password", _boom)

    with pytest.raises(RuntimeError):
        asyncio.run(security.hash_password_async("Passw0rd123"))

    assert events == ["acquire", "release"], (
        f"expected the permit to be released in a finally block, got {events}"
    )


# ---------------------------------------------------------------------------
# #21 — accepting an application must not overbook the trip
# ---------------------------------------------------------------------------
def _make_trip(client, user, **overrides) -> dict:
    payload = {
        "title": "Kyoto autumn trip",
        "description": "Looking for a companion to see the autumn colours.",
        "destination_country": "Japan",
        "destination_city": "Kyoto",
        "budget_type": "MODERATE",
        "target_gender": "ANY",
        "tags": ["PHOTOGRAPHY"],
        "looking_for_count": 1,
    }
    payload.update(overrides)
    resp = client.post("/api/v1/trips", headers=user["headers"], json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _apply(client, trip, user) -> dict:
    resp = client.post(
        f"/api/v1/trips/{trip['id']}/apply",
        headers=user["headers"],
        json={"message": "Hi"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_accept_transition_is_a_compare_and_set(client, register_user):
    """The decision must be expressed as `UPDATE ... WHERE status='PENDING'`.

    The status transition is what makes a decision final. An unconditional write
    lets two concurrent deciders both apply, which is the part of the TOCTOU that
    actually overbooks the trip — the capacity count can be correct and the trip
    still ends up with an extra companion, because both writes land.
    """
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    trip = _make_trip(client, alice, looking_for_count=1)
    app_id = _apply(client, trip, bob)["id"]

    first = client.patch(
        f"/api/v1/trips/applications/{app_id}?decision=ACCEPTED",
        headers=alice["headers"],
    )
    assert first.status_code == 200, first.text

    # Replaying the same decision is idempotent (200); a *different* one is 409.
    replay = client.patch(
        f"/api/v1/trips/applications/{app_id}?decision=ACCEPTED",
        headers=alice["headers"],
    )
    assert replay.status_code == 200, replay.text

    flip = client.patch(
        f"/api/v1/trips/applications/{app_id}?decision=REJECTED",
        headers=alice["headers"],
    )
    assert flip.status_code == 409, (
        f"settled application accepted a contradictory decision: {flip.status_code}"
    )


def test_capacity_is_rechecked_after_the_lock(client, register_user):
    """Accepting must count *after* serialising, or two accepts both see room.

    Single-threaded this only proves the check still happens; the value is that
    it pins the read to the post-lock state, so removing the re-read (leaving only
    the pre-lock count) is visible. An overbooked trip is the kind of defect that
    raises nothing and is only found by counting.
    """
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, looking_for_count=1)
    applicants = [register_user(nickname=f"A{i}") for i in range(2)]
    ids = [_apply(client, trip, u)["id"] for u in applicants]

    assert client.patch(
        f"/api/v1/trips/applications/{ids[0]}?decision=ACCEPTED",
        headers=alice["headers"],
    ).status_code == 200

    second = client.patch(
        f"/api/v1/trips/applications/{ids[1]}?decision=ACCEPTED",
        headers=alice["headers"],
    )
    assert second.status_code == 409, second.text

    detail = client.get(f"/api/v1/trips/{trip['id']}", headers=alice["headers"]).json()
    accepted = [a for a in detail.get("applications", []) if a["status"] == "ACCEPTED"]
    assert len(accepted) == 1, (
        f"trip advertises looking_for_count=1 but has {len(accepted)} accepted — "
        "the capacity check did not run against the committed state"
    )


def test_concurrent_accepts_do_not_overbook(register_user):
    """**The test that actually pins #21.** Two accepts in flight at once.

    Everything above is sequential, and a sequential test cannot see this bug:
    the second decision reads the first's *committed* count and the check passes
    for the wrong reason. The race needs two requests genuinely interleaved, which
    means driving the ASGI app through `httpx.ASGITransport` inside one loop
    rather than through `TestClient` (whose `__enter__`/`__exit__` serialise).

    Measured against the unfixed code: both PATCHes returned 200 and the trip
    ended with 2 accepted companions on a `looking_for_count=1` trip — no error,
    no log, just an overbooked trip discoverable only by counting.

    The failure is asserted from both sides, per the project rule: the server must
    reject one request **and** the persisted state must show exactly one accepted
    companion. Asserting only the statuses would pass if the rejected write had
    already been applied.
    """
    import httpx

    from app.main import app

    alice = register_user(nickname="Alice")
    applicants = [register_user(nickname=f"Racer{i}") for i in range(2)]

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            # Build the fixture through the same client the race will use.
            owner = alice["headers"]
            created = await c.post(
                "/api/v1/trips",
                headers=owner,
                json={
                    "title": "Concurrent accept probe",
                    "description": "Two accepts fired at the same instant.",
                    "destination_country": "Japan",
                    "destination_city": "Osaka",
                    "budget_type": "MODERATE",
                    "target_gender": "ANY",
                    "tags": ["FOOD"],
                    "looking_for_count": 1,
                },
            )
            assert created.status_code == 201, created.text
            trip_id = created.json()["id"]

            app_ids = []
            for applicant in applicants:
                applied = await c.post(
                    f"/api/v1/trips/{trip_id}/apply",
                    headers=applicant["headers"],
                    json={"message": "hi"},
                )
                assert applied.status_code == 201, applied.text
                app_ids.append(applied.json()["id"])

            # The race: both decisions in flight, no await between them.
            responses = await asyncio.gather(*[
                c.patch(
                    f"/api/v1/trips/applications/{aid}?decision=ACCEPTED",
                    headers=owner,
                )
                for aid in app_ids
            ])

            detail = await c.get(f"/api/v1/trips/{trip_id}", headers=owner)
            accepted = [
                a for a in detail.json().get("applications", [])
                if a["status"] == "ACCEPTED"
            ]
            return [r.status_code for r in responses], accepted

    statuses, accepted = asyncio.run(run())

    assert sorted(statuses) == [200, 409], (
        f"expected exactly one accept and one rejection, got {statuses} — with both "
        "at 200 the trip is silently overbooked"
    )
    assert len(accepted) == 1, (
        f"{len(accepted)} applications ended ACCEPTED on a looking_for_count=1 trip — "
        "the capacity check raced"
    )


def test_concurrent_contradictory_decisions_on_one_application(register_user):
    """The CAS half of #21: one application must not be decided twice.

    Distinct from overbooking. The serialising lock prevents two *different*
    applications passing the capacity check; it does nothing for the same
    application being accepted and rejected at once, because both requests take
    the lock in turn and each finds a free slot.

    Without the conditional UPDATE this returns `200, 200` and the row ends
    **REJECTED after having been accepted** — two contradictory decisions both
    reported as success, and the applicant gets both notifications. Measured.
    """
    import httpx

    from app.main import app

    alice = register_user(nickname="Alice")
    applicant = register_user(nickname="Racer")

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            created = await c.post(
                "/api/v1/trips",
                headers=alice["headers"],
                json={
                    "title": "Contradictory decision probe",
                    "description": "One application, two decisions at once.",
                    "destination_country": "Japan",
                    "budget_type": "MODERATE",
                    "target_gender": "ANY",
                    "tags": ["FOOD"],
                    # Plenty of room, so the capacity check cannot be what
                    # rejects the loser — this test is about the CAS alone.
                    "looking_for_count": 5,
                },
            )
            trip_id = created.json()["id"]
            applied = await c.post(
                f"/api/v1/trips/{trip_id}/apply",
                headers=applicant["headers"],
                json={"message": "hi"},
            )
            app_id = applied.json()["id"]

            responses = await asyncio.gather(
                c.patch(
                    f"/api/v1/trips/applications/{app_id}?decision=ACCEPTED",
                    headers=alice["headers"],
                ),
                c.patch(
                    f"/api/v1/trips/applications/{app_id}?decision=REJECTED",
                    headers=alice["headers"],
                ),
            )

            detail = await c.get(f"/api/v1/trips/{trip_id}", headers=alice["headers"])
            statuses = [a["status"] for a in detail.json()["applications"]]
            return [r.status_code for r in responses], statuses

    codes, final_statuses = asyncio.run(run())

    # Exactly one decision may win. Both at 200 means the row took two
    # contradictory decisions and the loser's outcome was discarded without
    # telling anyone.
    assert sorted(codes) == [200, 409], (
        f"expected one decision to win and one to be refused, got {codes} — with "
        "both at 200 a settled application takes a contradictory decision"
    )

    # Which decision wins is **not** defined: the two requests are issued
    # simultaneously, so the scheduler picks the order and either ACCEPT or
    # REJECT may land first. Asserting `["ACCEPTED"]` made this test flaky at
    # ~17% (measured: `codes=[409, 200] final=['REJECTED']` on 2 of 12 runs) —
    # it was pinning an ordering guarantee that does not exist, not a bug.
    #
    # The real invariant is that the persisted row equals *the winner's*
    # decision, and that the loser was told it lost. That is what makes the
    # decision durable and mutually exclusive.
    assert len(final_statuses) == 1, f"expected one application, got {final_statuses}"
    winner = "ACCEPTED" if codes[0] == 200 else "REJECTED"
    assert final_statuses == [winner], (
        f"the first request ({codes[0]}) wanted {winner}, but the row ended as "
        f"{final_statuses} — the losing write overwrote the winner, so the "
        "decision was not durable"
    )
    assert final_statuses != ["PENDING"], (
        "both requests were refused yet the application is still PENDING — "
        "the CAS rejected every writer"
    )


def test_a_refused_accept_does_not_settle_the_application(client, register_user):
    """A 409 must leave the application PENDING, not silently ACCEPTED.

    The check has to happen before the write. If the status is set first and the
    capacity check raises afterwards, the request reports failure while the row
    says accepted — the applicant is "in" on an overbooked trip and no error
    anywhere reflects it.
    """
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, looking_for_count=1)
    applicants = [register_user(nickname=f"A{i}") for i in range(2)]
    ids = [_apply(client, trip, u)["id"] for u in applicants]

    client.patch(
        f"/api/v1/trips/applications/{ids[0]}?decision=ACCEPTED",
        headers=alice["headers"],
    )
    refused = client.patch(
        f"/api/v1/trips/applications/{ids[1]}?decision=ACCEPTED",
        headers=alice["headers"],
    )
    assert refused.status_code == 409

    detail = client.get(f"/api/v1/trips/{trip['id']}", headers=alice["headers"]).json()
    statuses = {a["id"]: a["status"] for a in detail.get("applications", [])}
    assert statuses[ids[1]] == "PENDING", (
        f"a refused accept settled the application anyway: {statuses}"
    )


# ---------------------------------------------------------------------------
# #22a — the circuit breaker window must be shareable across replicas
# ---------------------------------------------------------------------------
class _FakeBreakerBackend:
    """Stands in for the Redis-backed shared breaker."""

    def __init__(self) -> None:
        self.store: dict = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, mapping: dict) -> None:
        self.store.update(mapping)


def test_breaker_window_is_adopted_from_another_replica():
    """A replica that has not yet failed must inherit the outage.

    Without sharing, each replica discovers the outage independently and each
    pays its own round of connection timeouts — with a KV lookup on the login
    path, that is N rounds of multi-second stalls instead of one.
    """
    backend = _FakeBreakerBackend()
    replica_a = _fresh_kv_module("kv_a")
    replica_b = _fresh_kv_module("kv_b")

    for module in (replica_a, replica_b):
        module.set_breaker_backend(backend)

    assert replica_b._breaker.is_open() is False

    # Replica A hits the outage and publishes the window.
    replica_a._trip_breaker(RuntimeError("connection refused"))

    assert replica_b._breaker.is_open() is True, (
        "replica B did not adopt the outage — it will pay its own timeout round"
    )
    assert replica_b._redis() is None, "the inherited window must short-circuit Redis"


def test_breaker_still_works_with_no_shared_backend():
    """The shared backend is an enhancement, never a dependency.

    A breaker that requires Redis in order to learn that Redis is down is worse
    than no breaker. The default must therefore keep working with no backend at
    all — which is also the dev/test configuration.
    """
    replica = _fresh_kv_module("kv_solo")
    assert replica._breaker.is_open() is False

    replica._trip_breaker(RuntimeError("boom"))

    assert replica._breaker.is_open() is True
    assert replica._redis() is None


def test_broken_shared_backend_does_not_break_the_breaker():
    """A backend that raises must fall back to local state, not propagate."""

    class _AngryBackend:
        def get(self, key):
            raise RuntimeError("backend is also down")

        def set(self, mapping):
            raise RuntimeError("backend is also down")

    replica = _fresh_kv_module("kv_angry")
    replica.set_breaker_backend(_AngryBackend())

    # The read must not raise...
    assert replica._breaker.is_open() is False
    # ...and the trip must still work locally.
    replica._trip_breaker(RuntimeError("boom"))
    assert replica._breaker.is_open() is True


def test_rate_limit_breaker_state_is_also_shareable():
    """`rate_limit` exposes the same seam, since it guards the same Redis."""
    replica = _fresh_rate_limit_module("rl_a")
    backend = _FakeBreakerBackend()
    replica.set_breaker_backend(backend)

    replica._trip_breaker(RuntimeError("boom"))

    assert backend.store.get("deadline", 0) > time.time(), (
        "the rate-limit breaker did not publish its window"
    )


# ---------------------------------------------------------------------------
# B7 — the shared breaker window must survive being read by another process
#
# `time.monotonic()` has no common epoch across processes. Publishing it and
# comparing it on the reading side produced two opposite failures: a replica
# whose monotonic clock had advanced further saw the window as already expired
# (and hammered the dead Redis), one whose clock started later saw it as "far in
# the future" (and stayed tripped for hours). The fix publishes wall-clock and
# converts to a duration on read. These tests pin both halves.
# ---------------------------------------------------------------------------
def test_published_breaker_window_is_a_wall_clock_deadline():
    """Publishing must not leak `monotonic()` into the shared backend.

    A monotonic value and a wall-clock value are both floats, so nothing about
    the type system prevents the swap — only an assertion on the magnitude does.
    A monotonic value is seconds-since-boot (small, and on a container often
    under a day); a wall-clock deadline is a ~1.7e9 Unix timestamp. Writing the
    monotonic one here would be adopted by peers as a deadline in 1970, i.e.
    already expired, which is exactly the outage-storm this fix removes.
    """
    backend = _FakeBreakerBackend()
    replica = _fresh_kv_module("kv_wallclock")
    replica.set_breaker_backend(backend)

    replica._trip_breaker(RuntimeError("connection refused"))

    published = backend.store.get("deadline")
    assert published is not None, "the window was not published at all"
    assert published > 1_000_000_000, (
        f"published deadline {published!r} looks like a monotonic value, not a "
        "Unix timestamp — peers will read it as long expired"
    )
    delta = published - time.time()
    assert 0 < delta <= replica._COOLDOWN_SECONDS, (
        f"published window is {delta:.1f}s away; expected within one cooldown "
        f"({replica._COOLDOWN_SECONDS}s)"
    )
    assert replica._breaker.cooldown_until < 1_000_000_000, (
        "the LOCAL window must stay monotonic — converting it to wall-clock "
        "would make the local check vulnerable to a clock step"
    )


def test_a_peer_whose_monotonic_clock_differs_still_adopts_the_window():
    """The adoption must not depend on the two replicas sharing a clock base.

    Simulated by giving the second replica a `monotonic()` that is offset by
    hours — a stand-in for two containers booted at different times, which is
    the normal case in a rolling deploy. With the old code the offset decided
    the outcome; with the fix it is irrelevant, because the comparison is
    wall-clock on both sides.
    """
    backend = _FakeBreakerBackend()
    publisher = _fresh_kv_module("kv_pub", redis=None)
    follower = _fresh_kv_module("kv_follow", redis=None)
    for module in (publisher, follower):
        module.set_breaker_backend(backend)

    publisher._trip_breaker(RuntimeError("connection refused"))

    # The follower's monotonic clock is 6 hours behind the publisher's. Under
    # the old "compare published monotonic against local monotonic" scheme this
    # made the window look 6 hours long.
    real_monotonic = follower.time.monotonic
    offset = -6 * 3600.0

    def skewed_monotonic():
        return real_monotonic() + offset

    follower.time.monotonic = skewed_monotonic
    try:
        adopted = follower._breaker.is_open()
    finally:
        follower.time.monotonic = real_monotonic

    assert adopted is True, (
        "a replica with a different monotonic clock base failed to adopt the "
        "outage — it will pay its own round of connection timeouts"
    )
    remaining = backend.store["deadline"] - time.time()
    assert 0 < remaining <= follower._COOLDOWN_SECONDS + 1, (
        "the window was not bounded by one cooldown"
    )


def test_an_expired_shared_deadline_does_not_keep_the_breaker_open():
    """A stale key must not extend the outage.

    The published hash outlives the request that wrote it (`_TTL` is a multiple
    of the cooldown). If the reader blindly trusts a deadline that has already
    passed — or lets a clock skew past it in the other direction — the breaker
    never closes and Redis is never re-probed after it recovers.
    """
    backend = _FakeBreakerBackend()
    replica = _fresh_kv_module("kv_stale")
    replica.set_breaker_backend(backend)

    backend.set({"deadline": time.time() - 3600, "disabled": False})

    assert replica._breaker.is_open() is False, (
        "an already-expired shared deadline kept the breaker open — Redis would "
        "never be re-probed"
    )


def test_a_skewed_far_future_deadline_is_clamped_to_one_cooldown():
    """A peer whose clock is badly ahead must not freeze everyone else.

    Without the clamp, one replica with a wrong clock publishes a deadline
    hours out and every other replica dutifully stays tripped for that whole
    time — the breaker turns a 30-second blip into an hours-long outage.
    """
    backend = _FakeBreakerBackend()
    replica = _fresh_kv_module("kv_skew")
    replica.set_breaker_backend(backend)

    backend.set({"deadline": time.time() + 6 * 3600, "disabled": False})
    replica._breaker.is_open()  # triggers adoption

    remaining = replica._breaker.cooldown_until - time.monotonic()
    assert 0 < remaining <= replica._COOLDOWN_SECONDS, (
        f"a far-future deadline was adopted verbatim ({remaining:.0f}s) — one "
        "skewed peer would keep every replica's breaker open"
    )


def test_a_non_numeric_shared_deadline_is_ignored_not_fatal():
    """The backend is a stringly-typed hash; a bad value must not raise."""
    backend = _FakeBreakerBackend()
    replica = _fresh_kv_module("kv_garbage")
    replica.set_breaker_backend(backend)

    backend.set({"deadline": "not-a-number", "disabled": False})

    assert replica._breaker.is_open() is False


# ---------------------------------------------------------------------------
# #22b — seeding must be safe to run from several replicas
# ---------------------------------------------------------------------------
def _noop_for_async():
    async def _inner(*args, **kwargs):
        return None

    return _inner


class _SeedSpySession:
    """Minimal async session double: records statements, answers "no rows".

    Answering "no rows" for every `execute` is what makes the seed take its
    insert path, which is the path the lock has to guard. `flush` and `add` are
    accepted and ignored — this test is about the lock, not persistence.
    """

    def __init__(self, executed: list[str]) -> None:
        self._executed = executed

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params=None):
        self._executed.append(str(statement))
        return _EmptyResult()

    async def flush(self):
        return None

    def add(self, obj):
        return None

    def add_all(self, objs):
        return None

    async def commit(self):
        return None


class _EmptyResult:
    def scalar_one_or_none(self):
        return None

    def scalar(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []


def _patch_seed(monkeypatch, seed_module, url: str, executed: list[str]) -> None:
    async def _noop_init_db():
        return None

    monkeypatch.setattr(seed_module.settings, "DATABASE_URL", url)
    monkeypatch.setattr(seed_module, "init_db", _noop_init_db)
    monkeypatch.setattr(seed_module, "AsyncSessionLocal", lambda: _SeedSpySession(executed))
    monkeypatch.setattr(seed_module, "_seed_cities", _noop_for_async())


def test_seed_takes_an_advisory_lock_on_postgresql(monkeypatch):
    """The seed must be mutually exclusive, not merely idempotent.

    Per-row existence checks make a *sequential* second run a no-op, but they do
    nothing for concurrent runs: every replica reads "absent" before any row is
    committed, then all of them insert. The lock is what makes the second pass
    happen after the first has committed.
    """
    from app import seed as seed_module

    executed: list[str] = []
    _patch_seed(monkeypatch, seed_module, "postgresql+asyncpg://x/y", executed)

    asyncio.run(seed_module.seed())

    joined = " | ".join(executed)
    assert "pg_advisory_lock" in joined, (
        f"the seed did not take an advisory lock — concurrent replicas will "
        f"duplicate or crash: {executed}"
    )
    assert "pg_advisory_unlock" in joined, (
        f"the advisory lock was never released; a pooled connection would carry "
        f"it into its next user: {executed}"
    )


def test_seed_does_not_use_advisory_locks_on_sqlite(monkeypatch):
    """SQLite has no advisory lock; the branch must be an honest no-op.

    Emitting `pg_advisory_lock` against SQLite would be a runtime error, and
    pretending the lock exists would be a fake guarantee — the seed is run by
    hand on that dialect.
    """
    from app import seed as seed_module

    executed: list[str] = []
    _patch_seed(monkeypatch, seed_module, "sqlite+aiosqlite:///x.db", executed)

    asyncio.run(seed_module.seed())

    joined = " | ".join(executed)
    assert "pg_advisory" not in joined, (
        f"a PostgreSQL-only lock was issued on SQLite: {executed}"
    )
