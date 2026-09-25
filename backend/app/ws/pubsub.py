"""Cross-replica fan-out for WebSocket rooms (technical debt #6).

The problem
-----------
`ws/manager.py` keeps its room table in process memory. With one replica that is
fine. With two — and **`uvicorn --workers 4` is already two** — a socket held by
replica A is invisible to a broadcast issued on replica B. The user in A never
sees the message the user in B sent, and *nothing reports an error*: the sender
gets a normal ack, the database row is written, and the message is simply not
delivered to half the room.

Room membership had the same shape of defect — `online_profiles` could only
answer for the replica that was asked — but it is *not* fixed here, because a
pub/sub channel cannot answer it: a subscriber only hears messages published
while it is connected, so a replica that joins later learns nothing about who is
already present. Membership therefore lives in `services/kv.PresenceRegistry`,
which stores one TTL'd entry per replica and answers with their union. Read that
module for the liveness story (why a plain shared set would show ghosts).

This module is the missing half: when a manager broadcasts, it (1) delivers to
its own sockets and (2) publishes the frame to a per-room Redis channel. Every
replica — including the publisher, via its own subscription — receives the
channel message and delivers it to *its* local sockets. Redis Pub/Sub is the
right primitive because chat fan-out is ephemeral: a replica that is down has no
sockets to deliver to, so there is nothing to durably queue.

The degradation contract
------------------------
Same shape as `services/kv.py`, and for the same reason — a naive "try Redis,
fall back on error" makes *every* call pay the failure cost, and on Windows
`localhost` costs ~4 s before IPv4 is tried. So:

1. Explicit short socket timeouts, so one attempt cannot hang.
2. A circuit breaker: after a failure, skip Redis for a cooldown and behave as a
   single replica. Only the first call after an outage pays.
3. Lazy connection, so importing this module never touches the network.

**With Redis unavailable the behaviour is exactly today's single-process
behaviour** — local delivery only. That is the honest trade: correctness within
one replica, no cross-replica delivery, and a warning that says so. It is
strictly better than failing the broadcast, because the messages that *can* be
delivered still are.

Own-echo is suppressed by an instance id
----------------------------------------
A replica receives its own published messages back from Redis. Delivering them
again would double-send every message to every local socket, so each frame
carries the originating `instance_id` and a replica drops frames it published
itself — it has already delivered those locally.

Redis Pub/Sub is per-process and not durable — restarting a replica loses its
subscription, so `subscribe()` re-establishes it with a retry loop rather than
assuming it survives.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from app.core.config import settings

logger = logging.getLogger("tripmate.ws.pubsub")

# Same discipline as kv.py: a local, low-latency dependency that either answers
# quickly or is not going to.
_CONNECT_TIMEOUT = 0.5
_SOCKET_TIMEOUT = 1.0
_COOLDOWN_SECONDS = 30.0

# Channel prefix, so one Redis instance can host several environments without
# a frame intended for staging landing on a production socket.
_CHANNEL_PREFIX = "tripmate:ws:room:"

# How long to wait before re-subscribing after the connection drops. Short,
# because a replica that is not subscribed is silently half-deaf.
_RESUBSCRIBE_DELAY = 1.0

# Upper bound on how long `start()` waits for the subscription to be live.
# Bounded on purpose: startup must not depend on Redis being reachable, so this
# is a best-effort signal, not a requirement.
_SUBSCRIBE_WAIT = 0.5

FrameHandler = Callable[[str, dict[str, Any], str], Awaitable[None]]
"""Called as `handler(room_id, payload, origin_instance_id)`."""

# One id per process. Read as the default for `RoomPubSub.instance_id`, so two
# workers of the same deployment never share it — which is exactly the property
# own-echo suppression needs.
#
# It is a *default* rather than a constant used directly, because a single
# process can legitimately hold more than one `RoomPubSub` (tests do, and so
# would a future per-room-shard design). Two instances sharing an id in one
# process would each treat the other's frames as their own echo and drop them —
# a silent cross-replica failure that only appears once the process is split.
INSTANCE_ID = uuid.uuid4().hex


class RoomPubSub:
    """Publishes room frames to Redis and feeds inbound frames to a handler.

    Deliberately transport-only: it knows nothing about sockets, rooms or
    membership. The manager owns delivery; this owns the wire.
    """

    def __init__(self, *, instance_id: str | None = None) -> None:
        self._redis = None
        self._redis_disabled = False     # permanently unusable (bad URL / no package)
        self._cooldown_until = 0.0       # temporarily skipping Redis after a failure
        self._pubsub = None
        self._listen_task: asyncio.Task | None = None
        self._handler: FrameHandler | None = None
        self._stopping = False
        self._last_local_only_warning = 0.0
        # Created in `start()`, NOT here. An `asyncio.Event` binds to the event
        # loop that first awaits it, and the module-level singleton is
        # constructed at *import* time — before any loop exists. Binding it here
        # made every test after the first fail with "is bound to a different
        # event loop", because each TestClient gets a fresh loop while the
        # singleton is shared for the whole process.
        self._subscribed: asyncio.Event | None = None
        # Identifies *this* transport when filtering own echoes. Defaults to the
        # process-wide id, which is what a real deployment wants.
        self.instance_id = instance_id or INSTANCE_ID

    # --- state ------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Best-effort *prediction* of whether the next publish will leave.

        This is cheap and non-blocking, so it can be logged at startup — but it
        is only a prediction. It cannot know whether Redis is actually reachable
        until something tries, and it deliberately stays `True` on a cold start
        rather than probing the network from a property. **Do not use it as
        evidence that a frame reached another replica**: use the return value of
        `publish()`, which reports what happened.
        """
        return not self._redis_disabled and not self._breaker_open()

    def note_local_only_delivery(self, room_id: str) -> None:
        """Log, at most once per cooldown, that a broadcast stayed local.

        Without this the failure is invisible from the outside: the sender gets
        a normal ack and the database row is written, so a message that reached
        only half the room looks exactly like one that reached all of it.
        Rate-limited by the same cooldown the breaker uses, so a busy room does
        not turn one outage into a log flood.
        """
        now = time.monotonic()
        if now < self._last_local_only_warning + _COOLDOWN_SECONDS:
            return
        self._last_local_only_warning = now
        logger.warning(
            "WebSocket fan-out: broadcast in room %s was delivered to this "
            "replica only (Redis unavailable). Users on other replicas will NOT "
            "receive it.",
            room_id,
        )

    def _breaker_open(self) -> bool:
        return time.monotonic() < self._cooldown_until

    def _trip_breaker(self, exc: Exception) -> None:
        first = not self._breaker_open()
        self._cooldown_until = time.monotonic() + _COOLDOWN_SECONDS
        if first:
            logger.warning(
                "WebSocket fan-out: Redis unavailable (%s: %s) — cross-replica "
                "delivery is OFF for %ss. Sockets on other replicas will not "
                "receive frames during this window.",
                type(exc).__name__,
                exc,
                int(_COOLDOWN_SECONDS),
            )

    def _client(self):
        """Lazily construct the async Redis client; None when unusable."""
        if self._redis_disabled or self._breaker_open():
            return None
        if self._redis is not None:
            return self._redis
        try:
            from redis.asyncio import Redis

            self._redis = Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=_CONNECT_TIMEOUT,
                socket_timeout=_SOCKET_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 — missing package or malformed URL
            logger.warning(
                "WebSocket fan-out: Redis client cannot be created (%s) — "
                "running as a single replica.",
                exc,
            )
            self._redis_disabled = True
            self._redis = None
        return self._redis

    # --- publishing -------------------------------------------------------

    async def publish(self, room_id: str, frame: dict[str, Any]) -> bool:
        """Publish a frame to the room's channel. False when it did not leave.

        `frame` is the caller's own payload-plus-metadata and is placed under the
        envelope's `payload` key **as-is** — no re-wrapping. The envelope is the
        transport's business (`origin` for own-echo suppression); everything else
        belongs to the manager, and the two must not be nested inside each other
        or the receiver unpacks the wrong layer.

        A `False` return is not an error: it means this process is the only
        replica that will deliver the frame, which is exactly what the caller
        should then do locally.
        """
        client = self._client()
        if client is None:
            return False
        envelope = json.dumps({"origin": self.instance_id, "payload": frame})
        try:
            await client.publish(self._channel(room_id), envelope)
            return True
        except Exception as exc:  # noqa: BLE001 — degrade to local-only
            self._trip_breaker(exc)
            return False

    # --- subscribing ------------------------------------------------------

    def set_handler(self, handler: FrameHandler) -> None:
        self._handler = handler

    async def start(self) -> None:
        """Begin listening. Safe to call when Redis is absent — it just idles.

        Returns as soon as the subscription is established, or after a bounded
        `_SUBSCRIBE_WAIT` if it is not. Startup therefore never blocks on Redis
        being reachable, but a caller can still observe the difference — see
        `subscribed`.
        """
        if self._handler is None:
            raise RuntimeError("set_handler() must be called before start()")
        if self._listen_task is not None:
            return
        self._stopping = False
        # Bound to the running loop here, never at construction (see `__init__`).
        self._subscribed = asyncio.Event()
        self._listen_task = asyncio.create_task(self._listen_forever())
        # Wait, briefly, for the subscription to be established. Not for
        # correctness — frames published before that are still delivered locally
        # — but so a caller can *distinguish* "listening" from "racing to
        # listen". Without this, a test (or an operator reading startup logs)
        # cannot tell a live subscription from one that has not been attempted,
        # and the difference is the whole point of the feature.
        event = self._subscribed
        if event is not None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=_SUBSCRIBE_WAIT)

    @property
    def subscribed(self) -> bool:
        """Whether the subscription is live *right now*.

        Distinct from `enabled`, which only predicts whether a publish will be
        attempted. A replica can be momentarily deaf (between a dropped
        connection and the re-subscribe) while `enabled` still reads True; this
        reports the fact.
        """
        return self._subscribed is not None and self._subscribed.is_set()

    async def stop(self) -> None:
        self._stopping = True
        if self._subscribed is not None:
            self._subscribed.clear()
        task, self._listen_task = self._listen_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._close()

    async def _close(self) -> None:
        pubsub, self._pubsub = self._pubsub, None
        if pubsub is not None:
            with contextlib.suppress(Exception):
                await pubsub.aclose()
        client, self._redis = self._redis, None
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()

    async def _listen_forever(self) -> None:
        """Subscribe, read, and re-subscribe on failure until stopped.

        A dropped subscription is the dangerous state: the replica keeps
        accepting sockets and publishing, but no longer receives anything, so
        its users go deaf while everything appears healthy. Hence the loop
        rather than a single `async for`.
        """
        while not self._stopping:
            client = self._client()
            if client is None:
                # Redis is unusable right now; wait out the cooldown and retry
                # rather than exiting, so recovery needs no restart.
                await asyncio.sleep(_RESUBSCRIBE_DELAY)
                continue
            try:
                pubsub = client.pubsub()
                await pubsub.psubscribe(f"{_CHANNEL_PREFIX}*")
                self._pubsub = pubsub
                if self._subscribed is not None:
                    self._subscribed.set()
                logger.info("WebSocket fan-out: subscribed to %s*", _CHANNEL_PREFIX)
                async for message in pubsub.listen():
                    if self._stopping:
                        break
                    await self._dispatch(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — reconnect
                # Clear first: between the drop and the re-subscribe this
                # replica is deaf, and anything asking must see that.
                if self._subscribed is not None:
                    self._subscribed.clear()
                self._trip_breaker(exc)
                await self._close()
                await asyncio.sleep(_RESUBSCRIBE_DELAY)
            else:
                # `listen()` ended without an exception: the connection closed
                # cleanly. Still a deaf window until re-subscribed.
                if self._subscribed is not None:
                    self._subscribed.clear()

    async def _dispatch(self, message: dict[str, Any]) -> None:
        # `psubscribe` yields subscribe/pmessage confirmations mixed with data.
        if message.get("type") != "pmessage":
            return
        channel = message.get("channel") or ""
        room_id = channel[len(_CHANNEL_PREFIX):]
        if not room_id:
            return
        try:
            envelope = json.loads(message.get("data") or "{}")
        except (TypeError, ValueError):
            logger.warning("WebSocket fan-out: dropped a malformed frame on %s", channel)
            return

        # Own-echo: this replica already delivered the frame locally before
        # publishing, so delivering it again would double-send every message.
        if envelope.get("origin") == self.instance_id:
            return

        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            return
        if self._handler is not None:
            await self._handler(room_id, payload, envelope.get("origin", ""))

    @staticmethod
    def _channel(room_id: str) -> str:
        return f"{_CHANNEL_PREFIX}{room_id}"


pubsub = RoomPubSub()
