"""WebSocket connection manager + per-user message rate limiting (§3.2).

Room state lives here (sockets cannot be shared between processes), but the
*fan-out* is delegated to `ws/pubsub.py` so a frame published on one replica
reaches sockets held by another. See that module for the degradation contract.

**Two things that look like room state but are not.**

*The message quota.* A user's "2 messages/second" is a per-*user* budget, and a
user can hold sockets on more than one replica (two tabs landing on different
workers, or a reconnect). Counting it in a per-process dict — which this file
used to do — gives 2 msg/s *per replica*, so the effective limit silently scales
with the replica count and changes as the load balancer reshuffles. The tokens
therefore live in `services.kv.DistributedTokenBucket`, which is shared when
Redis is up and degrades to a per-process bucket when it is not. The arithmetic
is identical either way; only the scope changes.

*Presence.* "Who is in this room" is also a question about the whole deployment,
not about this process — a room whose members are split across two workers would
otherwise report half of them, with the half depending on the balancer. It is
delegated to `services.kv.PresenceRegistry`, which stores one TTL'd entry *per
replica* and answers with their union. The per-replica key is what gives presence
a liveness story: a replica that dies never calls `disconnect`, so a plain shared
set would show its members forever, whereas an entry that stops being refreshed
ages out.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from app.services.kv import DistributedTokenBucket, PresenceRegistry, default_replica_id
from app.ws.pubsub import RoomPubSub, pubsub

logger = logging.getLogger("tripmate.ws.manager")

# §3.2 — 2 messages/second per user. Capacity equals the rate, so a brief burst
# of two is allowed and then the caller is throttled to the sustained rate.
_DEFAULT_MSG_RATE = 2.0

# Key namespace. Includes the limit so changing the policy starts a fresh bucket
# instead of inheriting a half-spent one from the previous configuration.
_BUCKET_PREFIX = "tripmate:ws:bucket:"

# How long a replica's presence entry survives without a heartbeat. Must be a
# multiple of `_HEARTBEAT_SECONDS`: the margin is what absorbs a missed beat
# (event-loop stall, GC pause) without the replica's members blinking out.
_HEARTBEAT_SECONDS = 15.0
_PRESENCE_STALE_AFTER = 45


class ConnectionManager:
    """Tracks live sockets per room and enforces send-rate limits.

    The fan-out transport is injected rather than imported at call time. The
    module-level singleton is the production default, but a hard reference to it
    would make this class untestable without patching module globals — and worse,
    it would hide the fact that *there is exactly one transport per process*
    behind a name lookup. Passing it in makes that assumption explicit.

    The message-rate bucket is injectable for the same reason, and because the
    quota is a *policy*: tests need to drive a known rate without waiting on a
    two-token refill.
    """

    def __init__(
        self,
        *,
        msg_rate_per_second: float = _DEFAULT_MSG_RATE,
        transport: "RoomPubSub | None" = None,
        bucket: DistributedTokenBucket | None = None,
        presence: PresenceRegistry | None = None,
        heartbeat_enabled: bool = False,
    ) -> None:
        # room_id -> {profile_id -> WebSocket}
        self._rooms: dict[str, dict[str, WebSocket]] = defaultdict(dict)
        self._lock = asyncio.Lock()
        self._msg_rate = msg_rate_per_second
        self._transport = transport if transport is not None else pubsub
        self._bucket = bucket if bucket is not None else DistributedTokenBucket(
            rate=msg_rate_per_second, capacity=msg_rate_per_second
        )
        # Presence is the *shared* half of room state: the sockets above cannot
        # leave this process, but "who is in this room" is a question about the
        # whole deployment. A fresh replica id per manager instance is deliberate
        # — two managers in one process (tests, or a second transport) must not
        # share a presence key, or each would treat the other's members as its
        # own and a disconnect could erase a live entry.
        self._presence = (
            presence
            if presence is not None
            else PresenceRegistry(
                replica_id=default_replica_id(), stale_after=_PRESENCE_STALE_AFTER
            )
        )
        self._heartbeat_task: asyncio.Task[None] | None = None
        # Off unless the application asks for it. A background timer is only
        # meaningful for a long-lived server, and starting one implicitly makes
        # every short-lived loop that merely *uses* a manager responsible for
        # cancelling a task it never requested. The lifespan turns it on for the
        # real server; tests and embedded uses leave it off, or opt in explicitly.
        self._heartbeat_enabled = heartbeat_enabled

    @property
    def presence(self) -> PresenceRegistry:
        """The shared presence registry (exposed for tests and shutdown)."""
        return self._presence

    async def connect(self, room_id: str, profile_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._rooms[room_id][profile_id] = websocket
        await self._presence.join(room_id, profile_id)
        if self._heartbeat_enabled:
            self._ensure_heartbeat()

    async def disconnect(self, room_id: str, profile_id: str) -> None:
        async with self._lock:
            room = self._rooms.get(room_id)
            if room:
                room.pop(profile_id, None)
                if not room:
                    self._rooms.pop(room_id, None)
        await self._presence.leave(room_id, profile_id)

    async def allow_message(self, profile_id: str) -> bool:
        """Whether this user may send a message right now.

        Async because the honest implementation needs the shared store: a
        synchronous check could only consult process-local state, which is the
        bug this replaces. Callers are already in an async request path.
        """
        return await self._bucket.allow(f"{_BUCKET_PREFIX}{self._msg_rate}:{profile_id}")

    def is_member_online(self, room_id: str, profile_id: str) -> bool:
        """Whether *this replica* holds a socket for the member.

        Synchronous and local on purpose: it answers "can I deliver to them right
        now?", which is a routing question about this process. For "is this
        member anywhere in the room?" — the question the `presence` frame asks —
        use `online_profiles`, which consults the shared registry.
        """
        return profile_id in self._rooms.get(room_id, {})

    def local_profiles(self, room_id: str) -> list[str]:
        """Members whose sockets *this* replica holds. No shared lookup.

        Split out from `online_profiles` so the heartbeat can publish the
        authoritative local list without recursion into the shared read.
        """
        return list(self._rooms.get(room_id, {}).keys())

    async def online_profiles(self, room_id: str) -> list[str]:
        """Everyone in the room, across every live replica.

        Union of this replica's sockets and the non-expired entries published by
        the others. Falls back to the local list when Redis is unavailable, which
        is the pre-#6 behaviour — under-reporting, never a failure.

        Async because the answer now genuinely lives somewhere else; a caller
        must not be able to accidentally treat the local slice as the whole room.
        """
        return await self._presence.online(room_id, local=self.local_profiles(room_id))

    # -- heartbeat ---------------------------------------------------------
    def _ensure_heartbeat(self) -> None:
        """Start the presence heartbeat on first connection.

        Lazily started, and started *here* rather than in `__init__`, because a
        task needs a running event loop. Constructing one at import time binds it
        to whichever loop happens to exist first — which under TestClient means
        every subsequent test fails with "attached to a different loop". That
        exact mistake cost 95 tests in this codebase once already; the fix is to
        create loop-bound objects only when the loop is demonstrably running.
        """
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        """Refresh this replica's presence entries while it holds sockets.

        The loop exits on its own once the last room empties. That is not just an
        optimisation: a task that outlives the loop that created it is a leak,
        and "somebody must remember to call `shutdown()`" is not a guarantee —
        the tests construct managers directly and would never call it, leaving a
        pending task per test to be torn down by the loop's death, which is what
        produces the *"Task was destroyed but it is still pending"* noise. Ending
        the task when the state it maintains is gone makes correctness
        independent of anyone's bookkeeping.
        """
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_SECONDS)
                if not await self._beat_once():
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a beat failure must not kill the loop
            # Reaching here means `_beat_once` raised outside its own guard, which
            # would otherwise end the heartbeat silently and let this replica's
            # members expire while the process is healthy.
            logger.exception("presence heartbeat iteration failed; continuing")
            self._heartbeat_task = None
            self._ensure_heartbeat()

    async def _beat_once(self) -> bool:
        """Publish the local member list for every room this replica holds.

        Returns False when there is nothing left to beat for — the signal for
        `_heartbeat_loop` to end.
        """
        async with self._lock:
            if not self._rooms:
                return False
            snapshot = {room_id: list(members) for room_id, members in self._rooms.items()}
        for room_id, members in snapshot.items():
            await self._presence.heartbeat(room_id, members)
        return True

    async def shutdown(self) -> None:
        """Stop the heartbeat and release this replica's presence entries.

        Called on app shutdown. Removing the entries eagerly (rather than waiting
        for the TTL) means a rolling restart does not leave the old replica's
        members visible for up to `stale_after` seconds alongside the new one's.
        """
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            rooms = list(self._rooms.keys())
        for room_id in rooms:
            await self._presence.forget_replica(room_id)


    async def broadcast(self, room_id: str, payload: dict[str, Any], *, exclude: str | None = None) -> None:
        """Deliver to every socket in the room, across all replicas.

        Two steps, and the order matters:

        1. deliver to this replica's sockets — this is the step that must not
           depend on Redis, so a Redis outage degrades to today's single-process
           behaviour rather than to total silence;
        2. publish the frame so the *other* replicas deliver it to theirs.

        `exclude` is carried inside the published payload rather than applied
        here, because the excluded profile may be connected to a different
        replica — the replica that owns the socket has to make the decision.
        """
        await self.deliver_local(room_id, payload, exclude=exclude)

        # The decision is made on the *result* of publishing, not on
        # `pubsub.enabled`. `enabled` is a prediction made without touching the
        # network, so on a cold start with Redis down it reads True while the
        # publish fails — and the degradation warning below would never fire.
        # Acting on the return value means the log reflects what actually
        # happened rather than what was expected.
        #
        # The published frame is exactly what `on_remote_frame` expects to
        # unpack: the original payload plus the `exclude` filter. `exclude`
        # travels with the frame because each replica applies it against its own
        # sockets — applying it only here would let the excluded user receive the
        # message via a replica that never heard of `exclude`.
        delivered_remotely = await self._transport.publish(
            room_id, {"payload": payload, "exclude": exclude}
        )
        if not delivered_remotely and self._has_local_members(room_id):
            # Say it once per broadcast, not once per socket: with several
            # replicas this is the difference between "some messages are late"
            # and "half the room never gets messages".
            self._transport.note_local_only_delivery(room_id)

    async def deliver_local(
        self, room_id: str, payload: dict[str, Any], *, exclude: str | None = None
    ) -> None:
        """Deliver only to sockets this process holds. Never publishes.

        Separate from `broadcast` so the inbound pub/sub handler can call it
        without re-publishing — a re-publish would loop the frame between
        replicas forever.
        """
        room = dict(self._rooms.get(room_id, {}))
        dead: list[str] = []
        for profile_id, ws in room.items():
            if exclude and profile_id == exclude:
                continue
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001 — socket gone
                dead.append(profile_id)
        for profile_id in dead:
            await self.disconnect(room_id, profile_id)

    def _has_local_members(self, room_id: str) -> bool:
        return bool(self._rooms.get(room_id))

    async def on_remote_frame(
        self, room_id: str, envelope: dict[str, Any], _origin: str
    ) -> None:
        """Pub/sub callback: a frame published by another replica.

        `payload` and `exclude` are unpacked from the envelope produced by
        `broadcast`; malformed envelopes are ignored rather than raising inside
        the listener loop, where an exception would kill the subscription.
        """
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            return
        exclude = envelope.get("exclude")
        await self.deliver_local(
            room_id, payload, exclude=exclude if isinstance(exclude, str) else None
        )

    async def send_personal(self, room_id: str, profile_id: str, payload: dict[str, Any]) -> None:
        ws = self._rooms.get(room_id, {}).get(profile_id)
        if ws is None:
            return
        try:
            await ws.send_json(payload)
        except Exception:  # noqa: BLE001
            await self.disconnect(room_id, profile_id)

    def room_id_of(self, websocket: WebSocket) -> str | None:
        for room_id, members in self._rooms.items():
            for ws in members.values():
                if ws is websocket:
                    return room_id
        return None


manager = ConnectionManager(heartbeat_enabled=True)
