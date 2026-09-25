"""Cross-replica WebSocket fan-out (technical debt #6).

What is actually being tested
-----------------------------
The failure this guards against is silent. With `uvicorn --workers 2` the room
table is per process, so a message sent by a user on replica A is acked, written
to the database, and never delivered to the user on replica B. No error, no log,
no failed request — just half a conversation. Nothing in the single-process test
suite can see it, because with one replica every socket is local and the bug is
invisible by construction.

These tests therefore build **two managers** and connect them through a broker,
which is the smallest arrangement that can distinguish "delivered to every
socket" from "delivered to the sockets I happen to hold".

On the fake broker
------------------
Redis is not reachable in this environment (it is not part of the test
dependencies), so the transport is replaced with an in-process broker that speaks
the same protocol: `publish(channel, envelope)` → every subscriber's handler.
That is enough to pin *our* logic — the envelope shape, own-echo suppression,
`exclude` propagation, local delivery — which is where the bugs would be.

It is deliberately **not** enough to prove Redis Pub/Sub works. What is not
covered: authentication, channel naming on a real server, reconnect after a
server-side disconnect, and the circuit breaker's behaviour against a genuinely
unreachable host. Those need a live Redis and belong in an integration job. The
tests below are careful never to claim more than that.
"""
from __future__ import annotations

import asyncio

import pytest

from app.ws import pubsub as pubsub_module
from app.ws.manager import ConnectionManager


async def _settle() -> None:
    """Let the pub/sub delivery task run to completion.

    Cross-replica delivery is **asynchronous**: `publish` hands the frame to
    Redis and returns; a separate listener task on the receiving replica then
    delivers it. Asserting immediately after `broadcast` therefore races the
    scheduler and fails intermittently — the frame *is* on its way.

    This is a property of the design, not a test artefact, and it is worth being
    explicit about: a caller cannot assume the moment `broadcast` returns that
    every replica has delivered. Yielding a few times is enough because the
    whole chain is in-process here; against a real Redis the delay is a network
    round trip, which is why nothing in the product depends on synchronous
    delivery.
    """
    for _ in range(5):
        await asyncio.sleep(0)


class FakeSocket:
    """Minimal stand-in for a FastAPI WebSocket."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def accept(self) -> None:
        pass

    async def send_json(self, payload: dict) -> None:
        if self.closed:
            raise RuntimeError("socket closed")
        self.sent.append(payload)

    def frames(self) -> list[dict]:
        return list(self.sent)


class FakeBroker:
    """In-process Pub/Sub: publish reaches every subscriber except the origin.

    Mirrors the two Redis behaviours our logic *depends* on:

    1. **Pattern subscriptions.** `psubscribe("prefix*")` must match every channel
       with that prefix, not just a channel literally named `"prefix"`. An exact
       dict lookup here was the first version of this fake and it silently
       matched nothing, so every cross-replica test failed while the production
       code was correct — a reminder that a fake which does not model the
       semantics of the thing it replaces tests the fake.
    2. **Self-delivery.** The publisher receives its own message back, which is
       what `INSTANCE_ID` exists to filter.
    """

    def __init__(self) -> None:
        self.subscribers: dict[str, list] = {}   # pattern -> [handler]
        self.published: list[tuple[str, str]] = []

    def psubscribe(self, pattern: str, handler) -> None:
        self.subscribers.setdefault(pattern, []).append(handler)

    async def publish(self, channel: str, envelope: str) -> int:
        self.published.append((channel, envelope))
        matched = 0
        for pattern, handlers in list(self.subscribers.items()):
            prefix = pattern[:-1] if pattern.endswith("*") else pattern
            if not channel.startswith(prefix):
                continue
            for handler in list(handlers):
                await handler(channel, envelope)
                matched += 1
        return matched


@pytest.fixture()
def broker(monkeypatch):
    """Point `RoomPubSub` at an in-process broker.

    Patches only the low-level client call, so the code under test is the real
    `publish` / `_dispatch` / envelope handling rather than a rewrite.
    """
    fake = FakeBroker()

    def fake_client(self):  # noqa: ANN001
        return _BrokerClient(fake)

    monkeypatch.setattr(pubsub_module.RoomPubSub, "_client", fake_client)
    return fake


class _BrokerClient:
    def __init__(self, broker: FakeBroker) -> None:
        self._broker = broker

    async def publish(self, channel: str, envelope: str) -> int:
        return await self._broker.publish(channel, envelope)

    def pubsub(self):
        return _BrokerPubSub(self._broker)

    async def aclose(self) -> None:
        pass


class _BrokerPubSub:
    def __init__(self, broker: FakeBroker) -> None:
        self._broker = broker
        self._pattern = ""

    async def psubscribe(self, pattern: str) -> None:
        assert pattern.endswith("*"), pattern
        # Keep the pattern intact: the broker matches on it, exactly as Redis
        # matches a pattern subscription against a concrete channel name.
        self._pattern = pattern

    async def listen(self):
        queue: asyncio.Queue = asyncio.Queue()

        async def handler(channel: str, envelope: str) -> None:
            await queue.put({"type": "pmessage", "channel": channel, "data": envelope})

        self._broker.psubscribe(self._pattern, handler)
        while True:
            yield await queue.get()

    async def aclose(self) -> None:
        pass


def _make_manager(*, transport=None) -> ConnectionManager:
    """A manager isolated from the process-wide transport.

    Tests must never touch the module-level singleton: it carries the process's
    real state, and a test that publishes into it leaks into every later test.
    """
    if transport is None:
        transport = pubsub_module.RoomPubSub(instance_id=f"test-{id(object())}")
    return ConnectionManager(transport=transport)


class _Instance:
    """One 'replica': a manager plus the pubsub object it publishes through."""

    def __init__(self, manager: ConnectionManager, transport) -> None:
        self.manager = manager
        self.transport = transport

    async def start(self, broker: FakeBroker) -> None:
        self.transport.set_handler(self.manager.on_remote_frame)
        await self.transport.start()

    async def stop(self) -> None:
        await self.transport.stop()


@pytest.fixture()
def two_replicas(broker):
    """Two independent replicas wired to the same broker.

    Each gets its own transport with its own `instance_id` — the property that
    makes own-echo suppression meaningful. A shared id would make the test pass
    while the real deployment double-sends every message, which is why
    `instance_id` is injectable rather than read from the module constant.

    The transport is injected into the manager rather than patched into module
    globals, so each replica genuinely owns its own transport — which is what
    makes "replica A's broadcast reached replica B's socket" a real claim.
    """

    def make(index: int) -> _Instance:
        transport = pubsub_module.RoomPubSub(instance_id=f"replica-{index}")
        return _Instance(ConnectionManager(transport=transport), transport)

    return make(0), make(1)


# --- the defect ------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_reaches_a_socket_on_another_replica(two_replicas, broker):
    """The core case: a frame published on replica A reaches replica B's socket.

    Without the fan-out this is exactly the silently-lost message: A's manager
    has no reference to B's socket, so `broadcast` walks an empty room and
    returns successfully.
    """
    a, b = two_replicas
    await a.start(broker)
    await b.start(broker)
    try:
        # Only replica B holds a socket for this room.
        from app.ws.pubsub import RoomPubSub

        socket = FakeSocket()
        await b.manager.connect("room-1", "profile-bob", socket)

        await a.manager.broadcast("room-1", {"type": "message", "content": "hello"})
        await _settle()

        assert [f["content"] for f in socket.frames()] == ["hello"], (
            "a frame broadcast on replica A never reached replica B's socket — "
            "this is the silent cross-replica message loss"
        )
    finally:
        await a.stop()
        await b.stop()


@pytest.mark.asyncio
async def test_broadcast_still_reaches_local_sockets(two_replicas, broker):
    """Local delivery must not regress — it is the path that must survive Redis.

    Also proves the message is not sent *twice* to a local socket, which is the
    own-echo failure: A delivers locally, then A's own published frame comes
    back through the broker and would be delivered again.
    """
    a, b = two_replicas
    await a.start(broker)
    await b.start(broker)
    try:
        socket = FakeSocket()
        await a.manager.connect("room-1", "profile-alice", socket)

        await a.manager.broadcast("room-1", {"type": "message", "content": "hi"})
        await _settle()

        assert len(socket.frames()) == 1, (
            f"expected exactly one delivery, got {len(socket.frames())} — "
            "more than one means own-echo was not suppressed"
        )
    finally:
        await a.stop()
        await b.stop()


@pytest.mark.asyncio
async def test_exclude_is_honoured_on_the_remote_replica(two_replicas, broker):
    """`typing` at A must not echo back to A's user when A's socket is on B.

    `exclude` is the *sender's* profile, and the sender's socket may be held by a
    different replica than the one issuing the broadcast. If `exclude` were
    applied only locally, a user would see their own typing indicator the moment
    their socket landed on another worker — which is what `--workers 2` does
    non-deterministically, making it an intermittent bug that is miserable to
    reproduce.
    """
    a, b = two_replicas
    await a.start(broker)
    await b.start(broker)
    try:
        # The excluded user's socket lives on B; the broadcast is issued on A.
        excluded = FakeSocket()
        other = FakeSocket()
        await b.manager.connect("room-1", "profile-alice", excluded)
        await b.manager.connect("room-1", "profile-bob", other)

        await a.manager.broadcast(
            "room-1",
            {"type": "typing", "profile_id": "profile-alice", "is_typing": True},
            exclude="profile-alice",
        )
        await _settle()

        assert excluded.frames() == [], "the excluded sender received their own frame"
        assert [f["type"] for f in other.frames()] == ["typing"]
    finally:
        await a.stop()
        await b.stop()


@pytest.mark.asyncio
async def test_remote_frame_is_not_republished(two_replicas, broker):
    """A frame received from the broker must not be published again.

    `on_remote_frame` calls `deliver_local`, not `broadcast`. If it published,
    two replicas would loop the same frame between themselves forever — an
    infinite message storm that a naive "just call broadcast" implementation
    produces. Asserted on the broker's publish count so it fails loudly.
    """
    a, b = two_replicas
    await a.start(broker)
    await b.start(broker)
    try:
        socket = FakeSocket()
        await b.manager.connect("room-1", "profile-bob", socket)

        before = len(broker.published)
        await b.manager.on_remote_frame("room-1", {"payload": {"type": "message"}}, "origin")
        await _settle()

        assert socket.frames(), "the remote frame was not delivered locally"
        assert len(broker.published) == before, (
            "handling a remote frame republished it — this is the infinite loop"
        )
    finally:
        await a.stop()
        await b.stop()


# --- the degradation contract ---------------------------------------------


@pytest.mark.asyncio
async def test_local_delivery_survives_a_publish_failure(monkeypatch):
    """When Redis is gone, local sockets must still receive their frames.

    The alternative — failing the broadcast — would turn a fan-out outage into a
    total chat outage, including for users who are connected and perfectly able
    to receive.
    """
    from app.ws.pubsub import RoomPubSub

    async def failing_publish(self, room_id, frame):  # noqa: ANN001
        return False

    monkeypatch.setattr(RoomPubSub, "publish", failing_publish)
    transport = RoomPubSub(instance_id="test-local-only")
    manager = ConnectionManager(transport=transport)
    socket = FakeSocket()
    await manager.connect("room-1", "profile-alice", socket)

    await manager.broadcast("room-1", {"type": "message", "content": "still here"})

    assert [f["content"] for f in socket.frames()] == ["still here"]


@pytest.mark.asyncio
async def test_degradation_is_logged_once_per_cooldown(monkeypatch, caplog):
    """A local-only broadcast must be *reported*, not silent.

    This is the whole reason the feature exists: from the sender's side a
    half-delivered message is indistinguishable from a delivered one. The log is
    the only external signal, so it has to fire — and fire at most once per
    cooldown, or a busy room turns one outage into a log flood.
    """
    import logging

    from app.ws.pubsub import RoomPubSub

    async def failing_publish(self, room_id, frame):  # noqa: ANN001
        return False

    monkeypatch.setattr(RoomPubSub, "publish", failing_publish)
    transport = RoomPubSub(instance_id="test-degraded")
    manager = ConnectionManager(transport=transport)
    await manager.connect("room-1", "profile-alice", FakeSocket())

    with caplog.at_level(logging.WARNING, logger="tripmate.ws.pubsub"):
        for _ in range(3):
            await manager.broadcast("room-1", {"type": "message"})

    warnings = [r for r in caplog.records if "delivered to this replica only" in r.message]
    assert len(warnings) == 1, f"expected exactly one degradation warning, got {len(warnings)}"


@pytest.mark.asyncio
async def test_malformed_remote_frame_is_ignored(broker):
    """A bad frame must not kill the listener.

    The handler runs inside the subscription loop, where an exception would end
    the loop and leave the replica permanently deaf while still accepting
    sockets. Ignoring a malformed frame is the only safe response.
    """
    from app.ws.pubsub import RoomPubSub

    received: list[tuple[str, dict, str]] = []

    async def handler(room_id, payload, origin):  # noqa: ANN001
        received.append((room_id, payload, origin))

    transport = RoomPubSub()
    transport.set_handler(handler)

    # `payload` is not a dict — the shape a version mismatch would produce.
    await transport._dispatch(
        {"type": "pmessage", "channel": "tripmate:ws:room:r1", "data": '{"origin":"x","payload":[]}'}
    )
    assert received == [], "a malformed frame was delivered"

    # Undecodable data must be survivable too.
    await transport._dispatch(
        {"type": "pmessage", "channel": "tripmate:ws:room:r1", "data": "not json"}
    )
    assert received == []


@pytest.mark.asyncio
async def test_own_echo_is_suppressed(broker, monkeypatch):
    """A replica must not process the frame it published itself.

    This is the guard that makes (1) local delivery and (2) publish safe to do in
    sequence. Without it every message would be delivered twice to every socket
    owned by the publisher.
    """
    from app.ws.pubsub import INSTANCE_ID, RoomPubSub

    received: list[str] = []

    async def handler(room_id, payload, origin):  # noqa: ANN001
        received.append(room_id)

    transport = RoomPubSub()
    transport.set_handler(handler)

    # Same origin as this process.
    await transport._dispatch(
        {
            "type": "pmessage",
            "channel": "tripmate:ws:room:r1",
            "data": f'{{"origin":"{INSTANCE_ID}","payload":{{"type":"message"}}}}',
        }
    )
    assert received == [], "own echo was delivered"

    # A different origin is a genuine remote frame.
    await transport._dispatch(
        {
            "type": "pmessage",
            "channel": "tripmate:ws:room:r1",
            "data": '{"origin":"someone-else","payload":{"type":"message"}}',
        }
    )
    assert received == ["r1"]
