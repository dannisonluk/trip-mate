"""Presence must describe the whole deployment, not this process (#6, final item).

The defect being guarded
------------------------
`ConnectionManager` holds sockets, and sockets cannot cross process boundaries.
So `online_profiles` could only ever answer "who is connected to *this* replica".
With `uvicorn --workers 2` a room whose members are split across the two workers
reports roughly half of them, and *which* half is decided by the load balancer —
so the `presence` frame every client receives is not merely incomplete, it is
non-deterministic. Nothing raises; the UI just shows the wrong list.

Why the fix is not "a shared set"
---------------------------------
The obvious repair — one Redis `SET` of profile ids per room — is wrong in a way
that only shows up in production: **a replica that crashes or is SIGKILLed never
runs `disconnect`**, so its members would sit in the shared set forever. Worse,
nothing in the system could distinguish such a ghost from a real, quiet member.
A liveness story is required, and the only entity that can supply one is the
replica itself: it must keep saying "these are mine, I am alive".

Hence one TTL'd entry per replica:

    tripmate:presence:{room}:{replica}  ->  "profile_a|profile_b"

refreshed by a heartbeat, and the read is the union of the entries that have not
expired. A dead replica stops refreshing and its entire entry ages out within one
stale window — ghosts are bounded by construction, not by bookkeeping.

Modeling a replica
------------------
As in `test_shared_quota.py`: **two objects in one process are not two
replicas.** They would share the module-level `_memory` store in `kv`, so a
per-instance dict would still look "shared" and every test here would pass
without proving anything. A replica is therefore a **fresh `kv` module** built by
`importlib`, each with its own globals, sharing only the fake Redis — which is
exactly what `--workers N` gives you.

What is and is not covered
--------------------------
There is no Redis here, so the store is faked. The fake models the properties
under test — keys are shared, sets round-trip, and TTL actually expires — but it
says nothing about real Redis: replication lag, clock skew between hosts, and
failover belong in an integration job. The expiry model in particular is driven
by `time.monotonic()`, so a test can advance it by patching the clock rather than
sleeping.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app.services import kv
from app.services.kv import PresenceRegistry, default_replica_id
from app.ws.manager import ConnectionManager

_KV_PATH = Path(kv.__file__)


class FakePresenceRedis:
    """A shared, TTL-aware Redis stub with the set ops presence needs.

    Deliberately a single object referenced by every replica, holding the whole
    keyspace. A per-replica dict would reproduce the #6 bug and make the union
    tests pass vacuously.
    """

    def __init__(self, *, clock=None) -> None:
        # key -> set of members
        self.sets: dict[str, set[str]] = {}
        # key -> absolute deadline (in whatever `clock` returns)
        self.expiry: dict[str, float] = {}
        self._clock = clock or _default_clock()
        # Counts of each command, so a test can prove the *shape* of the traffic
        # (e.g. that a heartbeat rewrites rather than merely extends).
        self.calls: list[str] = []

    # -- internals --------------------------------------------------------
    def _live(self, key: str) -> bool:
        deadline = self.expiry.get(key)
        if deadline is not None and self._clock() > deadline:
            self.sets.pop(key, None)
            self.expiry.pop(key, None)
            return False
        return True

    def _require(self, key: str) -> set[str]:
        if not self._live(key):
            return set()
        return self.sets.setdefault(key, set())

    # -- commands used by PresenceRegistry --------------------------------
    async def sadd(self, key: str, *members: str) -> int:
        self.calls.append("sadd")
        target = self._require(key)
        before = len(target)
        target.update(members)
        return len(target) - before

    async def srem(self, key: str, *members: str) -> int:
        self.calls.append("srem")
        target = self._require(key)
        removed = 0
        for member in members:
            if member in target:
                target.discard(member)
                removed += 1
        if not target:
            # Redis drops an empty set; matching that keeps the union read from
            # having to special-case an empty entry.
            self.sets.pop(key, None)
        return removed

    async def smembers(self, key: str) -> set[str]:
        self.calls.append("smembers")
        return set(self._require(key))

    async def keys(self, pattern: str) -> list[str]:
        self.calls.append("keys")
        # Only the `prefix*` form is used, so a simple startswith is faithful.
        prefix = pattern[:-1] if pattern.endswith("*") else pattern
        return [k for k in list(self.sets) if self._live(k) and k.startswith(prefix)]

    async def expire(self, key: str, ttl: int) -> bool:
        self.calls.append("expire")
        if not self._live(key):
            return False
        self.expiry[key] = self._clock() + ttl
        return True

    async def delete(self, key: str) -> int:
        self.calls.append("delete")
        existed = key in self.sets
        self.sets.pop(key, None)
        self.expiry.pop(key, None)
        return 1 if existed else 0

    def pipeline(self):
        return _FakePipeline(self)

    async def aclose(self) -> None:
        pass


class _FakePipeline:
    """Batches commands, executing in order on `execute()`.

    Faithful enough for the heartbeat's delete/sadd/expire sequence, which is the
    only pipeline presence uses; the point of the pipeline there is atomicity,
    which a test cannot observe on a stub and does not attempt to.
    """

    def __init__(self, fake: FakePresenceRedis) -> None:
        self._fake = fake
        self._queued: list[tuple] = []

    def delete(self, key: str):
        self._queued.append(("delete", (key,)))
        return self

    def sadd(self, key: str, *members: str):
        self._queued.append(("sadd", (key, *members)))
        return self

    def expire(self, key: str, ttl: int):
        self._queued.append(("expire", (key, ttl)))
        return self

    async def execute(self):
        results = []
        for name, args in self._queued:
            results.append(await getattr(self._fake, name)(*args))
        return results


def _default_clock():
    """A monotonic clock the TTL model can be built on."""
    import time

    return time.monotonic


class _TestClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _fresh_kv_module(name: str, redis):
    """Load an independent copy of `services.kv` bound to `redis`.

    Each call yields a module with its **own** `_memory`, `_redis_client` and
    `_breaker` — i.e. a separate worker process. Returning the same module twice
    would silently make the per-replica defect untestable.
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
def redis():
    """A shared fake plus the clock that controls its TTLs."""
    clock = _TestClock()
    return FakePresenceRedis(clock=clock), clock


def _registry(kv_module, redis_conn, *, replica_id: str, stale_after: int = 45):
    return kv_module.PresenceRegistry(replica_id=replica_id, stale_after=stale_after)


# --- the core claim: the read is a union across replicas --------------------


@pytest.mark.asyncio
async def test_presence_is_the_union_of_replicas(redis):
    """Two replicas each holding one member must both appear.

    This is the whole of #6. Before the fix, each replica could only see its own
    socket table, so one of these two members was invisible to the other —
    whichever one the balancer had put elsewhere.
    """
    conn, _ = redis
    kv_a = _fresh_kv_module("kv_presence_a", conn)
    kv_b = _fresh_kv_module("kv_presence_b", conn)

    replica_a = _registry(kv_a, conn, replica_id="replica-a")
    replica_b = _registry(kv_b, conn, replica_id="replica-b")

    await replica_a.join("room-1", "profile-a")
    await replica_b.join("room-1", "profile-b")

    # Each replica sees both, even though neither holds the other's socket.
    seen_by_a = await replica_a.online("room-1", local=["profile-a"])
    seen_by_b = await replica_b.online("room-1", local=["profile-b"])

    assert seen_by_a == ["profile-a", "profile-b"], (
        "replica A could not see replica B's member — presence is still per-replica"
    )
    assert seen_by_b == ["profile-a", "profile-b"]


@pytest.mark.asyncio
async def test_presence_does_not_leak_across_rooms(redis):
    """The union must be scoped to the room, not to the whole keyspace.

    A prefix that is too loose (`presence:*`) would merge every room into one
    list, which is a worse bug than the one being fixed: members of unrelated
    rooms would appear in each other's presence frames.
    """
    conn, _ = redis
    kv_a = _fresh_kv_module("kv_roomscope", conn)
    replica = _registry(kv_a, conn, replica_id="replica-a")

    await replica.join("room-1", "profile-a")
    await replica.join("room-2", "profile-b")

    assert await replica.online("room-1", local=[]) == ["profile-a"]
    assert await replica.online("room-2", local=[]) == ["profile-b"]


@pytest.mark.asyncio
async def test_online_profiles_is_sorted(redis):
    """The wire format must be stable.

    An unordered union's contents depend on set iteration order, which turns any
    test (or client-side diff) of the presence frame into a coin flip. Sorting is
    cheap and removes the question.
    """
    conn, _ = redis
    kv_a = _fresh_kv_module("kv_sorted", conn)
    replica = _registry(kv_a, conn, replica_id="replica-a")

    for profile in ["profile-c", "profile-a", "profile-b"]:
        await replica.join("room-1", profile)

    assert await replica.online("room-1", local=[]) == ["profile-a", "profile-b", "profile-c"]


# --- liveness: why a plain shared set would not do --------------------------


@pytest.mark.asyncio
async def test_dead_replica_ghosts_age_out(redis):
    """A replica that stops refreshing must disappear entirely.

    This is the property the naive shared-set fix lacks. Here replica B "dies"
    (we simply stop beating) and its entry must age out within `stale_after`,
    leaving no trace. Without the TTL, B's member would be a permanent ghost and
    nothing could ever distinguish it from a real quiet member.
    """
    conn, clock = redis
    kv_a = _fresh_kv_module("kv_ghost_a", conn)
    kv_b = _fresh_kv_module("kv_ghost_b", conn)

    replica_a = _registry(kv_a, conn, replica_id="replica-a", stale_after=45)
    replica_b = _registry(kv_b, conn, replica_id="replica-b", stale_after=45)

    await replica_a.join("room-1", "profile-a")
    await replica_b.join("room-1", "profile-b")

    assert await replica_a.online("room-1", local=["profile-a"]) == ["profile-a", "profile-b"]

    # Replica B is killed: no `leave`, no heartbeat — the pathological case.
    # A only keeps beating itself.
    clock.advance(46)
    await replica_a.heartbeat("room-1", ["profile-a"])

    assert await replica_a.online("room-1", local=["profile-a"]) == ["profile-a"], (
        "the dead replica's member linger as a ghost — presence has no liveness "
        "story, so a crashed replica is indistinguishable from a quiet member"
    )


@pytest.mark.asyncio
async def test_join_is_refreshed_by_heartbeat(redis):
    """A live replica must keep its entry alive past the stale window.

    The complementary failure to the ghost: if the heartbeat did not refresh the
    TTL, every member would blink out after 45s even though their replica is
    perfectly healthy — a self-inflicted outage.
    """
    conn, clock = redis
    kv_a = _fresh_kv_module("kv_refresh", conn)
    replica = _registry(kv_a, conn, replica_id="replica-a", stale_after=45)

    await replica.join("room-1", "profile-a")

    # Beat three times, each just before the window closes.
    for _ in range(3):
        clock.advance(40)
        await replica.heartbeat("room-1", ["profile-a"])

    assert await replica.online("room-1", local=["profile-a"]) == ["profile-a"], (
        "a healthy replica's entry expired — the heartbeat does not refresh the TTL"
    )


# --- leave must be surgical -------------------------------------------------


@pytest.mark.asyncio
async def test_leave_only_touches_this_replica(redis):
    """A `leave` on one replica must not erase the same profile on another.

    A user can hold two tabs on different workers. Removing them globally on the
    first disconnect would make the room's presence wrong in the *other*
    direction (a live member reported offline), and the second tab's replica would
    appear to have an empty-but-live entry.
    """
    conn, _ = redis
    kv_a = _fresh_kv_module("kv_leave_a", conn)
    kv_b = _fresh_kv_module("kv_leave_b", conn)

    replica_a = _registry(kv_a, conn, replica_id="replica-a")
    replica_b = _registry(kv_b, conn, replica_id="replica-b")

    # The same profile connected to both replicas (two tabs).
    await replica_a.join("room-1", "profile-shared")
    await replica_b.join("room-1", "profile-shared")

    await replica_a.leave("room-1", "profile-shared")

    assert await replica_b.online("room-1", local=["profile-shared"]) == ["profile-shared"], (
        "a disconnect on one replica erased the profile from the other — the "
        "second tab is still connected but now invisible"
    )


@pytest.mark.asyncio
async def test_leave_keeps_the_replica_entry_alive(redis):
    """Leaving one member must not evict the replica's other members.

    The TTL has to be re-asserted on `leave`: the replica is still alive and may
    hold others, so an entry that was allowed to expire would drop the remaining
    members early.
    """
    conn, clock = redis
    kv_a = _fresh_kv_module("kv_leave_ttl", conn)
    replica = _registry(kv_a, conn, replica_id="replica-a", stale_after=45)

    await replica.join("room-1", "profile-a")
    await replica.join("room-1", "profile-b")

    clock.advance(40)
    await replica.leave("room-1", "profile-a")

    # Just past the *original* deadline; only the refresh from `leave` saves it.
    clock.advance(10)

    assert await replica.online("room-1", local=[]) == ["profile-b"], (
        "leaving one member let the whole replica entry expire, dropping the "
        "members that are still connected"
    )


# --- heartbeat rewrites the list: self-healing ------------------------------


@pytest.mark.asyncio
async def test_heartbeat_repairs_a_lost_leave(redis):
    """A dropped `leave` must be corrected by the next beat.

    If the heartbeat only extended the TTL, a lost `leave` — Redis briefly down
    for that one call, or a teardown that did not complete — would leave a stale
    member visible for the rest of the window and then *forever*, since each
    subsequent beat would keep the entry alive. Rewriting the member list is what
    makes the union read converge on the truth.
    """
    conn, _ = redis
    kv_a = _fresh_kv_module("kv_selfheal", conn)
    replica = _registry(kv_a, conn, replica_id="replica-a", stale_after=45)

    await replica.join("room-1", "profile-a")
    await replica.join("room-1", "profile-b")

    # Simulate the lost leave: the entry still lists profile-b, but the replica's
    # authoritative local list no longer contains it.
    await replica.heartbeat("room-1", ["profile-a"])

    assert await replica.online("room-1", local=["profile-a"]) == ["profile-a"], (
        "the heartbeat did not rewrite the member list, so a lost `leave` leaves "
        "a stale member that every subsequent beat keeps alive forever"
    )


@pytest.mark.asyncio
async def test_heartbeat_rewrites_rather_than_extends(redis):
    """Guard the *shape* of the heartbeat, not just its outcome.

    `test_heartbeat_repairs_a_lost_leave` passes for a heartbeat that does a full
    rewrite. It would also pass for one that (a) deletes then re-adds, or (b)
    happened to call `srem` for members missing locally. This asserts the command
    sequence directly so the mutation "heartbeat only calls expire" is caught
    rather than accidentally satisfied by some other path.
    """
    conn, _ = redis
    kv_a = _fresh_kv_module("kv_rewrite", conn)
    replica = _registry(kv_a, conn, replica_id="replica-a", stale_after=45)

    await replica.join("room-1", "profile-a")
    conn.calls.clear()

    await replica.heartbeat("room-1", ["profile-a", "profile-b"])

    # A pipeline batches these; the fake records each as it executes.
    assert "delete" in conn.calls, "heartbeat did not clear the entry before rewriting"
    assert "sadd" in conn.calls, "heartbeat did not write the member list"
    assert "expire" in conn.calls, "heartbeat did not refresh the TTL"
    assert await replica.online("room-1", local=[]) == ["profile-a", "profile-b"]


@pytest.mark.asyncio
async def test_heartbeat_of_an_empty_room_clears_the_entry(redis):
    """A room that emptied must not stay in Redis.

    This is the loop's exit condition made observable: once the last member
    leaves there is nothing to beat for, and leaving an empty key behind (with a
    TTL being refreshed by nobody) is a slow leak of keyspace.
    """
    conn, _ = redis
    kv_a = _fresh_kv_module("kv_emptyroom", conn)
    replica = _registry(kv_a, conn, replica_id="replica-a")

    await replica.join("room-1", "profile-a")
    await replica.heartbeat("room-1", [])

    assert await replica.online("room-1", local=[]) == []
    assert conn.sets == {}, "an emptied room left its key behind"


# --- graceful shutdown ------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_removes_the_replica_entry(redis):
    """A graceful stop must clear its entries, not wait for the TTL.

    On a rolling restart the replacement replica would otherwise be visible
    alongside the dead one's members for up to `stale_after` seconds, so every
    room would briefly list everyone twice.
    """
    conn, _ = redis
    kv_a = _fresh_kv_module("kv_shutdown_a", conn)
    kv_b = _fresh_kv_module("kv_shutdown_b", conn)

    replica_a = _registry(kv_a, conn, replica_id="replica-a")
    replica_b = _registry(kv_b, conn, replica_id="replica-b")

    await replica_a.join("room-1", "profile-a")
    await replica_b.join("room-1", "profile-b")

    await replica_a.forget_replica("room-1")

    assert await replica_b.online("room-1", local=["profile-b"]) == ["profile-b"], (
        "the stopped replica's members are still in the union — a rolling restart "
        "shows every room twice"
    )


# --- degradation: presence must never raise ---------------------------------


@pytest.mark.asyncio
async def test_presence_degrades_to_local_when_redis_is_gone(redis):
    """Redis down must narrow the answer, never fail the call.

    `online_profiles` runs on the chat hot path (every join and every leave). An
    exception there would break the socket for a purely informational frame, so
    the degradation contract is: fall back to this replica's own list — i.e.
    exactly the pre-#6 behaviour — and under-report.
    """
    def boom():
        raise RuntimeError("redis gone")

    kv_a = _fresh_kv_module("kv_presence_down", FakePresenceRedis())
    kv_a._redis = boom                     # type: ignore[attr-defined]
    replica = _registry(kv_a, boom, replica_id="replica-a")

    # These must not raise.
    await replica.join("room-1", "profile-a")
    await replica.leave("room-1", "profile-a")
    await replica.heartbeat("room-1", ["profile-x"])

    seen = await replica.online("room-1", local=["profile-local"])

    assert seen == ["profile-local"], (
        "with Redis unavailable presence did not fall back to the local view; "
        "the caller got either an exception or an empty room"
    )


@pytest.mark.asyncio
async def test_join_and_leave_use_the_memory_store_when_redis_is_gone(redis):
    """The in-process fallback must still track membership.

    Falling back is only useful if the fallback works: with Redis down, a join
    followed by a read must show the member, and a leave must remove them. The
    memory store's set ops (`sadd`/`srem`/`smembers`) are what make this hold.
    """
    def boom():
        raise RuntimeError("redis gone")

    kv_a = _fresh_kv_module("kv_presence_memory", FakePresenceRedis())
    kv_a._redis = boom                     # type: ignore[attr-defined]
    replica = _registry(kv_a, boom, replica_id="replica-a")

    # `local` stands in for the sockets the manager actually holds; the fallback
    # read answers from the memory store, but only ever *adds* to this list.
    local = ["profile-a", "profile-b"]

    await replica.join("room-1", "profile-a")
    await replica.join("room-1", "profile-b")
    assert await replica.online("room-1", local=local) == ["profile-a", "profile-b"]

    await replica.leave("room-1", "profile-a")
    assert await replica.online("room-1", local=["profile-b"]) == ["profile-b"], (
        "the memory fallback did not track the leave"
    )


# --- identity ---------------------------------------------------------------


def test_default_replica_id_is_unique_per_call():
    """Two processes reusing a pid must not collide into one presence key.

    `os.getpid()` alone is not enough: after a restart the pid is often reused,
    and two hosts running the same container image can both have pid 1. Colliding
    keys would make each replica look like the other's ghost — one entry, two
    writers, and a `leave` that erases a live member.
    """
    ids = {default_replica_id() for _ in range(50)}

    assert len(ids) == 50, "replica ids collided — two replicas would share a presence key"


def test_replica_id_must_be_non_empty():
    """An empty replica id would produce a key ending in `:` — a valid-looking
    key that every such replica writes to. Fail at construction instead."""
    with pytest.raises(ValueError):
        PresenceRegistry(replica_id="")


# --- the manager wires it up ------------------------------------------------


@pytest.mark.asyncio
async def test_manager_online_profiles_spans_replicas(redis):
    """The manager's public read must consult the shared registry.

    Guards the seam: `PresenceRegistry` could be perfectly correct while
    `ConnectionManager.online_profiles` still returned only `local_profiles()`,
    and every test above would stay green.
    """
    conn, _ = redis
    kv_a = _fresh_kv_module("kv_mgr_a", conn)
    kv_b = _fresh_kv_module("kv_mgr_b", conn)

    manager_a = ConnectionManager(presence=_registry(kv_a, conn, replica_id="replica-a"))
    manager_b = ConnectionManager(presence=_registry(kv_b, conn, replica_id="replica-b"))

    await manager_a.presence.join("room-1", "profile-a")
    await manager_b.presence.join("room-1", "profile-b")

    # Manager A holds only profile-a locally, but must report both.
    manager_a._rooms["room-1"]["profile-a"] = object()  # type: ignore[assignment]

    assert await manager_a.online_profiles("room-1") == ["profile-a", "profile-b"]


@pytest.mark.asyncio
async def test_manager_connect_and_disconnect_write_presence(redis):
    """`connect`/`disconnect` must maintain the registry, not just the socket map.

    Without this the registry is dead code: presence would be shared in
    principle and empty in practice.

    Asserted through the **socket table being emptied**, not through
    `online_profiles` on this replica — a local read would return the member from
    the socket map alone and would stay green with `connect`'s registry write
    removed. What proves the write happened is that a *different* replica can see
    this one's member, and that it stops seeing it after the disconnect.
    """
    conn, _ = redis
    kv_a = _fresh_kv_module("kv_mgr_writes", conn)
    kv_b = _fresh_kv_module("kv_mgr_observer", conn)
    manager = ConnectionManager(presence=_registry(kv_a, conn, replica_id="replica-a"))
    # An observer holding no sockets at all: everything it reports came from the
    # shared registry.
    observer = _registry(kv_b, conn, replica_id="replica-observer")

    class _FakeWS:
        async def accept(self) -> None:
            pass

    ws = _FakeWS()
    await manager.connect("room-1", "profile-a", ws)  # type: ignore[arg-type]
    assert await observer.online("room-1", local=[]) == ["profile-a"], (
        "connect did not publish this replica's member to the registry"
    )

    await manager.disconnect("room-1", "profile-a")
    assert await observer.online("room-1", local=[]) == [], (
        "disconnect did not remove the member from the shared registry"
    )
    assert manager.local_profiles("room-1") == [], "the socket map still holds the member"
