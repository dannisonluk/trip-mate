"""Small async key-value store: Redis when reachable, in-process TTL dict otherwise.

Used for ephemeral state that must never touch the database — OTP codes, revoked
JWT ids, and the per-user quotas described below. Both callers need the same
semantics, so the Redis client and the fallback live here rather than being
duplicated per service.

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
replicas (OTP codes, revocation, **per-user quotas**) therefore needs Redis in
production — the fallback is a convenience, not a substitute. Quotas are a
special case because a *wrong* answer is worse than a missing one: being limited
per replica when you meant per user lets a caller have N× the quota. See
`DistributedTokenBucket` for how that is handled.
"""
from __future__ import annotations

import logging
import os
import time
import uuid

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

    def sadd(self, key: str, member: str, ttl: int) -> None:
        """Add a member to a set, refreshing the set's TTL.

        The set is stored as a `|`-joined string rather than a Python `set` so
        that it round-trips through the same `(value, expires)` tuple as every
        other key — one storage shape, one TTL mechanism, no second eviction path
        that could disagree with the first.
        """
        raw = self.get(key)
        members = set(raw.split("|")) if raw else set()
        members.add(member)
        self.set(key, "|".join(sorted(members)), ttl)

    def srem(self, key: str, member: str, ttl: int) -> None:
        raw = self.get(key)
        if not raw:
            return
        members = set(raw.split("|"))
        members.discard(member)
        if members:
            self.set(key, "|".join(sorted(members)), ttl)
        else:
            self.delete(key)

    def smembers(self, key: str) -> set[str]:
        raw = self.get(key)
        return set(raw.split("|")) if raw else set()

    def clear(self) -> None:
        self._data.clear()


_memory = _MemoryStore()
_redis_client = None


def _breaker_open() -> bool:
    return _breaker.is_open()


def _trip_breaker(exc: Exception) -> None:
    """Stop hammering Redis for a while after a failure."""
    first = _breaker.trip()
    if first:
        logger.warning(
            "Redis unavailable (%s: %s) — using the in-memory store for %ss.",
            type(exc).__name__,
            exc,
            int(_COOLDOWN_SECONDS),
        )


def _redis():
    """Lazily construct an async Redis client; returns None if unusable."""
    global _redis_client
    if _breaker.is_disabled() or _breaker.is_open():
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
        _breaker.disable()
        _redis_client = None
    return _redis_client


async def set_value(key: str, value: str, ttl: int) -> None:
    try:
        client = _redis()
        if client is not None:
            await client.set(key, value, ex=ttl)
            return
    except Exception as exc:  # noqa: BLE001 — degrade to memory
        _trip_breaker(exc)
    _memory.set(key, value, ttl)


async def get_value(key: str) -> str | None:
    try:
        client = _redis()
        if client is not None:
            value = await client.get(key)
            if value is not None:
                return value
    except Exception as exc:  # noqa: BLE001
        _trip_breaker(exc)
    return _memory.get(key)


async def delete_value(key: str) -> None:
    try:
        client = _redis()
        if client is not None:
            await client.delete(key)
    except Exception as exc:  # noqa: BLE001
        _trip_breaker(exc)
    _memory.delete(key)


async def increment(key: str, ttl: int) -> int:
    """Atomically increment a counter, creating it with `ttl` if absent."""
    try:
        client = _redis()
        if client is not None:
            value = await client.incr(key)
            if value == 1:
                await client.expire(key, ttl)
            return int(value)
    except Exception as exc:  # noqa: BLE001
        _trip_breaker(exc)
    return _memory.incr(key, ttl)


class DistributedTokenBucket:
    """A token bucket whose tokens live in Redis (technical debt #3 / #6).

    Why this exists
    ---------------
    A per-process token bucket is correct with one replica and wrong with two.
    The WebSocket send limit is 2 messages/second *per user*; keeping the tokens
    in `ConnectionManager._buckets` means a user with sockets on both replicas
    gets 2 msg/s from each — 4 msg/s — and, worse, the effective limit depends on
    which worker the load balancer happened to pick. The HTTP limiter has exactly
    the same defect when it falls back to `memory://` storage. Both are one
    problem, so both are fixed by one primitive here.

    Semantics
    ---------
    Token *state* is shared, so the arithmetic below deliberately mirrors
    `ws.manager.TokenBucket.allow`: refill by elapsed-time × rate, cap at
    capacity, spend one token per call. The read-modify-write is not atomic
    across replicas — two concurrent calls can both see the same token count and
    both be allowed. That is a **bounded** overshoot (at most one extra message
    per concurrent replica per refill interval), it never lets a caller under the
    limit be *denied*, and it costs nothing on the hot path. An atomic Lua
    script would remove the overshoot but would also make the whole quota
    Redis-dependent: with Redis down, every send would have to block or fail.

    That trade is the point of this class. Two behaviours, chosen by whether a
    failure has happened:

    * **Redis healthy** — state is shared and the limit is global.
    * **Redis unusable** — the circuit breaker is already open (see the module
      docstring), so we skip Redis entirely and fall back to a per-process
      bucket. The limit degrades to per-replica, which is strictly better than
      either blocking the user or letting the quota vanish.

    `last_used` is written on every call so the key expires once a caller goes
    quiet, instead of leaking one key per profile forever.
    """

    def __init__(self, *, rate: float, capacity: float, ttl: int | None = None) -> None:
        self.rate = float(rate)
        self.capacity = float(capacity)
        # Long enough that an active bucket never expires between messages, short
        # enough that idle keys are reclaimed. The +1s is for clock skew between
        # writing the state and Redis applying the expiry.
        self.ttl = int(ttl if ttl is not None else max(2, int(capacity / rate) + 1))

    async def allow(self, key: str) -> bool:
        # The whole Redis interaction sits inside one guard, including obtaining
        # the client: `_redis()` can itself raise (construction, a malformed URL,
        # a DNS failure), and guarding only the read would let that escape all
        # the way out of a WebSocket send path.
        try:
            client = _redis()
            if client is None:
                # Redis is down or deliberately skipped. Fall back rather than
                # deny — see the class docstring.
                return self._allow_memory(key)
            tokens, updated = await self._load(client, key)

            now = time.time()
            tokens = min(self.capacity, tokens + max(0.0, now - updated) * self.rate)
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0

            # Best effort: the decision is already made, so a failed *write*
            # must not turn an allowed message into a rejected one. It also must
            # not raise — hence inside the same try rather than its own.
            await client.set(key, f"{tokens!r}|{now!r}", ex=self.ttl)
            return allowed
        except Exception as exc:  # noqa: BLE001 — any storage failure
            _trip_breaker(exc)
            return self._allow_memory(key)

    async def _load(self, client, key: str) -> tuple[float, float]:
        raw = await client.get(key)
        if not raw:
            return self.capacity, time.time()
        if "|" not in raw:
            # A value written by something else (or an older format). Treat it as
            # a full bucket rather than crashing on a malformed entry.
            return self.capacity, time.time()
        tokens_raw, updated_raw = raw.split("|", 1)
        try:
            return float(tokens_raw), float(updated_raw)
        except ValueError:
            return self.capacity, time.time()

    def _allow_memory(self, key: str) -> bool:
        """Per-process fallback with the *same* arithmetic as the Redis path.

        The state is the same `tokens|updated` string the Redis path writes, kept
        in `_MemoryStore._data` so it inherits the TTL handling that already
        exists there. Sharing the encoding — rather than a second float-based
        format — means the two paths cannot drift, which matters because the
        fallback is exactly the path that only ever runs when something is
        already wrong.
        """
        now = time.time()
        raw = _memory.get(key)
        if not raw or "|" not in raw:
            tokens, updated = self.capacity, now
        else:
            tokens_raw, updated_raw = raw.split("|", 1)
            try:
                tokens, updated = float(tokens_raw), float(updated_raw)
            except ValueError:
                tokens, updated = self.capacity, now
            else:
                tokens = min(self.capacity, tokens + max(0.0, now - updated) * self.rate)
        allowed = tokens >= 1.0
        if allowed:
            tokens -= 1.0
        # Written on every call so an active bucket never expires between
        # messages, while an idle one is reclaimed by the TTL.
        _memory.set(key, f"{tokens!r}|{now!r}", self.ttl)
        return allowed


class DistributedSemaphore:
    """A counting semaphore whose permits live in Redis (technical debt #8 / #21).

    Why not `asyncio.Semaphore`
    ---------------------------
    An `asyncio.Semaphore` (or anyio's `CapacityLimiter`) bounds concurrency
    *within one process*. Both callers of this class need a bound that spans
    replicas:

    * **#8 — the Argon2 worker pool.** Each concurrent hash reserves
      `memory_cost = 64 MiB`. Capping the pool per-process is a memory bound that
      silently multiplies by the replica count: 8 slots x 4 replicas = 32 hashes =
      ~2 GB, which is how a container gets OOM-killed. The cap is meant to be a
      *memory budget for the whole deployment*, so it has to be a global number.
    * **#21 — trip capacity.** `looking_for_count` is a promise about a trip, not
      about a process. Replicas that each check "have we reached the cap?" locally
      both answer "no" and both accept → the trip is overbooked with no error.

    Semantics
    ---------
    `acquire()` claims one permit and returns True; it returns False immediately
    (never blocks) when the global cap is already reached, so the caller decides
    whether to queue, retry or reject. `release()` returns the permit.

    Permits are `INCR`/`DECR` on one Redis key. Both are atomic, which is what
    makes the cap hold across replicas. The TTL is a **leak valve**, not expiry
    semantics: if a replica dies while holding a permit, nothing decrements it, so
    the key would ratchet toward "always full". The TTL is refreshed on every
    acquisition, so a live deployment keeps one long-lived key while an abandoned
    one ages out and the cap self-heals. That is a deliberate trade — the
    alternative (a per-permit key + holder registry) is far more machinery, and a
    transiently *over*-permissive cap is much less harmful than a deadlock.

    Degradation
    -----------
    With Redis unavailable, the counter falls back to a per-process count via
    `_memory.incr`. The cap then applies per replica — the same scope
    degradation as the other primitives here, and a memory-budget overrun rather
    than a correctness failure. As with `DistributedTokenBucket`, the wrong
    answer is a *larger* cap, never a denial of service.
    """

    def __init__(self, *, limit: int, ttl: int = 120) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self.limit = int(limit)
        self.ttl = int(ttl)

    async def acquire(self, key: str) -> bool:
        try:
            client = _redis()
            if client is None:
                return self._acquire_memory(key)
            # INCR first, then decide: a read-then-write would let two replicas
            # both read `limit - 1` and both take the last permit.
            used = await client.incr(key)
            if used > self.limit:
                # Over the line — give the permit straight back rather than
                # leaving the counter inflated, which would keep it over the cap
                # for as long as the TTL.
                await client.decr(key)
                return False
            await client.expire(key, self.ttl)
            return True
        except Exception as exc:  # noqa: BLE001 — degrade, never raise
            _trip_breaker(exc)
            return self._acquire_memory(key)

    async def release(self, key: str) -> None:
        """Return a permit. Best-effort: a failure here must not reach the caller.

        A stranded permit is recoverable (the TTL reclaims the whole key), whereas
        an exception raised out of `release` in a `finally:` block would replace
        the original error with a confusing one.
        """
        try:
            client = _redis()
            if client is None:
                self._release_memory(key)
                return
            remaining = await client.decr(key)
            if remaining <= 0:
                # Nobody holds it any more — delete so the next acquisition
                # starts from a clean key instead of a stale TTL.
                await client.delete(key)
        except Exception as exc:  # noqa: BLE001
            _trip_breaker(exc)
            self._release_memory(key)

    def _acquire_memory(self, key: str) -> bool:
        # `_memory.incr` mirrors the Redis INCR, but only within this process.
        used = _memory.incr(key, self.ttl)
        if used > self.limit:
            _memory.set(key, str(used - 1), self.ttl)
            return False
        return True

    def _release_memory(self, key: str) -> None:
        raw = _memory.get(key)
        remaining = (int(raw) if raw is not None else 0) - 1
        if remaining <= 0:
            _memory.delete(key)
        else:
            _memory.set(key, str(remaining), self.ttl)


class PresenceRegistry:
    """Who is in a chat room, across replicas (technical debt #6, presence half).

    The problem
    -----------
    `ConnectionManager` holds sockets, and sockets cannot be shared between
    processes — so `online_profiles` could only ever answer "who is connected to
    *this* replica". With two workers the room's presence list silently reports
    about half the members, and which half depends on the load balancer.

    Why not a plain Redis SET
    -------------------------
    A single `SADD`/`SREM` set answers the read question but has no liveness
    story: **a replica that crashes or is killed never calls `disconnect`**, so
    its members stay in the set forever. The room would show ghosts forever, and
    nothing in the system could ever tell a ghost from a real quiet member. The
    same is true of a dropped socket whose teardown did not run.

    So presence is stored **per replica**, and each replica's entry carries a
    TTL that its own heartbeat refreshes:

        tripmate:presence:{room}:{replica}   -> "profile_a|profile_b"   (TTL = stale_after)

    Reading is the union of every replica key that has not expired. A dead
    replica stops refreshing and its whole entry ages out within one heartbeat
    window, so ghosts are bounded by construction rather than by bookkeeping.

    The trade, stated plainly: a member whose replica is alive but whose
    heartbeat stalls for longer than `stale_after` disappears from presence until
    the next beat. That is why the TTL is a heartbeat multiple, not a session
    lifetime — being briefly *absent* from a presence list is a cosmetic error,
    whereas a permanent ghost is a correctness one.

    Degradation
    -----------
    With Redis unavailable this becomes per-replica again — i.e. exactly today's
    behaviour, which is the correct thing to fall back to. Presence is never
    allowed to raise: it is called on the chat hot path (every join and leave),
    and an exception there would break the socket for a purely informational
    frame.
    """

    def __init__(self, *, replica_id: str, stale_after: int = 45) -> None:
        if not replica_id:
            raise ValueError("replica_id must be non-empty")
        # A stable identity for this process. Two transports in one process must
        # NOT share it — see `default_replica_id`.
        self.replica_id = replica_id
        self.stale_after = int(stale_after)

    # -- keys -------------------------------------------------------------
    def _key(self, room_id: str, replica_id: str | None = None) -> str:
        return f"tripmate:presence:{room_id}:{replica_id or self.replica_id}"

    def _prefix(self, room_id: str) -> str:
        return f"tripmate:presence:{room_id}:"

    # -- writes -----------------------------------------------------------
    async def join(self, room_id: str, profile_id: str) -> None:
        """Record that this replica holds a socket for `profile_id`."""
        try:
            client = _redis()
            if client is not None:
                key = self._key(room_id)
                # SADD then EXPIRE, not `sadd(ex=...)`: the plain form works on
                # every Redis version and both calls go to the same key.
                await client.sadd(key, profile_id)
                await client.expire(key, self.stale_after)
                return
        except Exception as exc:  # noqa: BLE001 — presence degrades, never raises
            _trip_breaker(exc)
        _memory.sadd(self._key(room_id), profile_id, self.stale_after)

    async def leave(self, room_id: str, profile_id: str) -> None:
        """Drop `profile_id` from this replica's entry.

        Only this replica's key is touched: another replica may legitimately hold
        the same profile on a second tab, and removing them from *its* entry
        would make that replica look stale-empty.
        """
        try:
            client = _redis()
            if client is not None:
                key = self._key(room_id)
                await client.srem(key, profile_id)
                # Keep the TTL: the replica is still alive and may hold others.
                await client.expire(key, self.stale_after)
                return
        except Exception as exc:  # noqa: BLE001
            _trip_breaker(exc)
        _memory.srem(self._key(room_id), profile_id, self.stale_after)

    async def heartbeat(self, room_id: str, profile_ids: list[str]) -> None:
        """Re-assert this replica's full member list and refresh its TTL.

        Rewriting the whole entry (rather than only extending the TTL) is what
        makes the *union* read self-healing: if a `leave` was lost — because the
        process died mid-teardown, or Redis was down for that one call — the next
        beat publishes the authoritative local list and the stale member is gone
        instead of lingering for the rest of the TTL window.
        """
        try:
            client = _redis()
            if client is not None:
                key = self._key(room_id)
                if profile_ids:
                    pipe = client.pipeline()
                    pipe.delete(key)
                    pipe.sadd(key, *profile_ids)
                    pipe.expire(key, self.stale_after)
                    await pipe.execute()
                else:
                    await client.delete(key)
                return
        except Exception as exc:  # noqa: BLE001
            _trip_breaker(exc)
        if profile_ids:
            _memory.set(self._key(room_id), "|".join(sorted(set(profile_ids))), self.stale_after)
        else:
            _memory.delete(self._key(room_id))

    # -- read -------------------------------------------------------------
    async def online(self, room_id: str, *, local: list[str]) -> list[str]:
        """The union of every live replica's members, plus this replica's own.

        `local` is passed in rather than read from Redis because the local view
        is authoritative for *this* process and already correct — going to Redis
        to learn about ourselves would add a round trip and an opportunity to be
        wrong when Redis is down.

        Returns a sorted list for a stable wire format: an unordered union would
        make the presence frame's contents depend on hash iteration order, which
        turns any test (or client diff) of it into a coin flip.
        """
        members = set(local)
        try:
            client = _redis()
            if client is None:
                return sorted(members)
            # KEYS is O(n) over the whole keyspace, which is fine at this scale
            # (a handful of rooms x replicas) and avoids maintaining a second
            # index that could itself go stale. Revisit with SCAN if the room
            # count reaches the thousands.
            keys = await client.keys(f"{self._prefix(room_id)}*")
            for key in keys:
                # Presence must not include a replica that is mid-restart and has
                # not beaten yet; the TTL does the ageing, this just reads.
                if isinstance(key, bytes):
                    key = key.decode("utf-8")
                raw = await client.smembers(key)
                for member in raw:
                    members.add(member.decode("utf-8") if isinstance(member, bytes) else member)
        except Exception as exc:  # noqa: BLE001 — degrade to the local view
            _trip_breaker(exc)
            return sorted(members)
        return sorted(members)

    async def forget_replica(self, room_id: str) -> None:
        """Remove this replica's entry entirely (used on graceful shutdown)."""
        try:
            client = _redis()
            if client is not None:
                await client.delete(self._key(room_id))
        except Exception as exc:  # noqa: BLE001
            _trip_breaker(exc)
        _memory.delete(self._key(room_id))


def default_replica_id() -> str:
    """A per-process identifier for the presence registry.

    `os.getpid()` plus a random suffix: the pid keeps two workers distinct on one
    host, and the suffix keeps two processes that happen to reuse a pid (after a
    restart, or across hosts) from colliding into one presence key — which would
    make each look like the other's ghost.
    """
    return f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


class RedisBreakerBackend:
    """Shares breaker state across replicas through Redis itself.

    The obvious objection — "the breaker needs Redis to tell it Redis is down" —
    is why this is a *publisher*, not a *source of truth*. `is_open()` already
    returns True from the local window before it ever consults this backend, and
    every read here is wrapped in try/except that falls back to local state. So:

    * Redis down → the local breaker trips as before, one replica at a time
      (unchanged behaviour, no regression).
    * Redis *comes back*, then goes down again → the replicas that already know
      publish it, and the others adopt the window immediately instead of each
      eating a fresh round of failing requests. That is the whole of #22a.

    State lives in one hash with a TTL so an abandoned entry cannot keep the
    cluster in a permanently-tripped breaker. It uses a **separate, short-timeout
    synchronous client** rather than the app's async client: reads happen on the
    hot path (`_redis()` consults the breaker on every call) and must not await.
    """

    _KEY = "tripmate:breaker:kv"
    _TTL = int(_COOLDOWN_SECONDS * 4)

    def __init__(self) -> None:
        self._client = None
        self._disabled = False

    def _conn(self):
        if self._disabled:
            return None
        if self._client is not None:
            return self._client
        try:
            import redis as redis_sync

            if not settings.REDIS_URL.startswith(("redis://", "rediss://", "unix://")):
                self._disabled = True
                return None
            self._client = redis_sync.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=_CONNECT_TIMEOUT,
                socket_timeout=_SOCKET_TIMEOUT,
            )
        except Exception:  # noqa: BLE001
            self._disabled = True
            self._client = None
        return self._client

    def get(self, key: str):
        # The caller (`_BreakerState.is_open`) already guards this, but a backend
        # that raises on a bare `get` is a trap for the next caller — and the
        # whole reason this class exists is to behave when Redis does not.
        try:
            client = self._conn()
            if client is None:
                return None
            raw = client.hget(self._KEY, key)
            if raw is None:
                return None
            if key == "disabled":
                return raw in ("1", "True", "true")
            return float(raw)
        except Exception:  # noqa: BLE001 — never propagate a breaker read failure
            return None

    def set(self, mapping: dict) -> None:
        try:
            client = self._conn()
            if client is None:
                return
            payload = {k: ("1" if v is True else "0" if v is False else str(v))
                       for k, v in mapping.items()}
            pipe = client.pipeline()
            pipe.hset(self._KEY, mapping=payload)
            pipe.expire(self._KEY, self._TTL)
            pipe.execute()
        except Exception:  # noqa: BLE001 — publishing is best-effort
            return


class _BreakerState:
    """Circuit-breaker state for the Redis client, with cross-replica sharing.

    The default is intentionally per-process (see the module docstring): the
    breaker exists so that one replica stops paying a 4-second connection timeout
    on every call, and a per-process window already achieves that. What it does
    *not* achieve is coordination — during an outage, each replica independently
    discovers it and each eats its own round of failing requests (#22a).

    A shared backend can be installed with `set_breaker_backend` to publish the
    outage to the others. It is an opt-in seam rather than a Redis dependency,
    because the whole point of the breaker is to work *while Redis is down* — a
    breaker that needs Redis to tell it Redis is down is worse than useless.

    Clock contract — the one thing a shared window has to get right:

    * `self.cooldown_until` is a **`time.monotonic()`** value, and only ever
      compared against `time.monotonic()`.
    * the **published** field is `deadline`, a **`time.time()`** value. The two
      clocks are not interchangeable between processes: `monotonic()` has no
      common epoch (on Linux it counts from boot), so publishing it lets a
      replica that restarted at the wrong moment either inherit a window that
      expires immediately or one that never ends. `time.time()` is the only
      clock two hosts agree on.
    * the deadline is converted to a *duration* on read and clamped to one
      cooldown, so a skewed peer cannot extend the outage for everyone.
    """

    #: Published field name. Changing it silently breaks cross-replica
    #: adoption — a peer that cannot read the field simply never adopts, which
    #: looks like a normal outage rather than an error.
    _DEADLINE_FIELD = "deadline"

    def __init__(self) -> None:
        self.cooldown_until = 0.0
        self.disabled = False
        self._shared = None

    def is_open(self) -> bool:
        if self._shared is not None:
            try:
                if self._shared.get("disabled"):
                    return True
                self._adopt_shared_deadline()
            except Exception:  # noqa: BLE001 — a broken backend must not break us
                pass
        return time.monotonic() < self.cooldown_until

    def _adopt_shared_deadline(self) -> None:
        """Adopt a remotely published deadline, translated into our own clock.

        The published value is **wall-clock** (`time.time() + cooldown`), not
        `time.monotonic()`. Those two clocks are not comparable across
        processes: `monotonic()` has no common epoch, so a value written by one
        process is meaningless arithmetic in another — and on Linux it counts
        from boot, so a replica that restarted *after* the publisher would read
        a stale monotonic value as "far in the future" and could stay tripped
        for hours, while a replica that restarted before it would see the
        window as already expired and hammer the dead Redis.

        Reading wall-clock back and comparing it against *our* wall-clock is
        well-defined, because both processes agree on what UTC now is (modulo
        NTP skew). We then hand the remaining time to the local `monotonic()`
        window, so the deadline we actually check is immune to a wall-clock
        step during the window.
        """
        published = self._shared.get(self._DEADLINE_FIELD)
        if not published:
            return
        try:
            remaining = float(published) - time.time()
        except (TypeError, ValueError):
            return

        # Clamp in both directions. A negative remaining means the window
        # already elapsed (or our clock is ahead) — adopting it would keep the
        # breaker open for a duration nobody asked for. A remaining larger than
        # one full cooldown means the publisher's clock is ahead, or the key
        # outlived its intended window; trusting it verbatim would let a single
        # skewed replica keep every other replica's breaker open indefinitely.
        if remaining <= 0:
            return
        candidate = time.monotonic() + min(remaining, _COOLDOWN_SECONDS)
        if candidate > self.cooldown_until:
            # Another replica learned about the outage; adopt it.
            self.cooldown_until = candidate

    def is_disabled(self) -> bool:
        if self.disabled:
            return True
        if self._shared is not None:
            try:
                return bool(self._shared.get("disabled"))
            except Exception:  # noqa: BLE001
                return False
        return False

    def trip(self) -> bool:
        """Open the breaker. Returns True if this call is the one that opened it."""
        first = not self.is_open()
        self.cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
        # Publish wall-clock, never `cooldown_until`: see `_adopt_shared_deadline`.
        self._publish(
            {
                self._DEADLINE_FIELD: time.time() + _COOLDOWN_SECONDS,
                "disabled": self.disabled,
            }
        )
        return first

    def disable(self) -> None:
        self.disabled = True
        # Carry whatever remains of the local window across as wall-clock, so a
        # peer that adopts this `disabled` flag does not also lose the deadline.
        remaining = max(0.0, self.cooldown_until - time.monotonic())
        self._publish({self._DEADLINE_FIELD: time.time() + remaining, "disabled": True})

    def rearm(self) -> None:
        self.cooldown_until = 0.0

    def _publish(self, snapshot: dict) -> None:
        if self._shared is None:
            return
        try:
            self._shared.set(snapshot)
        except Exception:  # noqa: BLE001 — publishing is best-effort
            pass


_breaker = _BreakerState()


def set_breaker_backend(backend) -> None:
    """Install a shared breaker backend (multi-replica deployments).

    `backend` needs only `get(key) -> value | None` and `set(mapping)`. Kept
    duck-typed on purpose: the production implementation can be a Redis hash, a
    memcached entry, or a file, without this module taking a dependency on it.
    """
    _breaker._shared = backend


def reset_breaker_for_tests() -> None:
    """Test helper — re-arm the breaker and forget any shared backend."""
    _breaker.rearm()
    _breaker.disabled = False
    _breaker._shared = None


def reset_memory_store() -> None:
    """Test helper — clears the in-process fallback and re-arms the breaker."""
    _memory.clear()
    _breaker.rearm()
