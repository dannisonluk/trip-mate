"""DIRECT (1:1) room identity.

A `DIRECT` room is *the* conversation between two people, so there must be
exactly one of them. The original implementation decided "does a room already
exist for this pair?" by loading the caller's DIRECT rooms and comparing member
sets in Python:

    candidates = SELECT ... WHERE room_type='DIRECT' AND member=me
    for room in candidates:
        if {m.profile_id for m in room.members} == {me, other}: return room
    # else insert

That is a read-then-write check, and two concurrent requests both read "nothing"
before either wrote. The pair then had two rooms, each holding half the
conversation, and since neither endpoint knew about the other there was no way
to merge them after the fact. The same defect existed in a *second* copy of the
logic in the trips router (`_ensure_direct_room`, called on accept), so even the
non-concurrent case could fork: accepting an application and opening the chat by
hand are different write paths that each made their own decision.

The fix is a stored, UNIQUE-indexed `direct_pair_key` plus a single shared
creation helper. These tests pin both halves: the identity rule, and the fact
that the two write paths agree.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.test_api_flow import _make_trip


def _open_direct(client, user, other, **extra):
    return client.post(
        "/api/v1/chat/rooms",
        headers=user["headers"],
        json={"room_type": "DIRECT", "other_profile_id": other["profile"]["id"], **extra},
    )


def _rooms(client, user) -> list[dict]:
    resp = client.get("/api/v1/chat/rooms", headers=user["headers"])
    assert resp.status_code == 200, resp.text
    return resp.json()


def _direct_rooms_between(client, user, other) -> list[dict]:
    """DIRECT rooms whose membership is exactly this pair."""
    pair = {user["profile"]["id"], other["profile"]["id"]}
    return [
        r
        for r in _rooms(client, user)
        if r["room_type"] == "DIRECT"
        and {m["profile_id"] for m in r["members"]} == pair
    ]


# --- the identity rule -----------------------------------------------------


def test_opening_the_same_direct_room_twice_reuses_it(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")

    first = _open_direct(client, alice, bob)
    assert first.status_code == 201, first.text

    for _ in range(3):
        again = _open_direct(client, alice, bob)
        assert again.status_code == 201, again.text
        assert again.json()["id"] == first.json()["id"], "a second room was created"

    assert len(_direct_rooms_between(client, alice, bob)) == 1


def test_the_room_is_the_same_whichever_party_opens_it(client, register_user):
    """The pair is a *set*, so both orderings must resolve to one room.

    This is what the sorted key buys: keying on unsorted order would let
    `A:open(B)` and `B:open(A)` create two rooms for the same relationship.
    """
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")

    from_alice = _open_direct(client, alice, bob)
    assert from_alice.status_code == 201, from_alice.text
    from_bob = _open_direct(client, bob, alice)
    assert from_bob.status_code == 201, from_bob.text

    assert from_alice.json()["id"] == from_bob.json()["id"]
    assert len(_direct_rooms_between(client, alice, bob)) == 1


def test_different_pairs_get_different_rooms(client, register_user):
    """The uniqueness must be per pair, not global."""
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    carla = register_user(nickname="Carla")

    ab = _open_direct(client, alice, bob).json()["id"]
    ac = _open_direct(client, alice, carla).json()["id"]
    bc = _open_direct(client, bob, carla).json()["id"]

    assert len({ab, ac, bc}) == 3, "rooms for different pairs collided"


def test_a_direct_room_and_a_trip_group_do_not_collide(client, register_user):
    """TRIP rooms (`direct_pair_key IS NULL`) must not trip the unique index.

    A non-partial unique index would make every TRIP room compete for one NULL
    value, so the second group room ever created would fail.

    **Honest limit:** on SQLite this test cannot distinguish a partial index from
    a non-partial one, because SQLite treats NULLs as distinct (verified: a
    non-partial unique index accepts repeated NULLs happily). The predicate is
    load-bearing on **PostgreSQL**, where it is not redundant. So this pins the
    *behaviour* that matters and is reachable here — TRIP and DIRECT rooms
    coexisting — while the predicate itself is not negatively validated by this
    suite. Recorded rather than papered over.
    """
    owner = register_user(nickname="Owner")
    other = register_user(nickname="Other")
    first_trip = _make_trip(client, owner)
    second_trip = _make_trip(client, owner)
    for trip in (first_trip, second_trip):
        resp = client.post(
            "/api/v1/chat/rooms",
            headers=owner["headers"],
            json={"room_type": "TRIP", "trip_post_id": trip["id"]},
        )
        assert resp.status_code == 201, resp.text

    # ...and DIRECT rooms still work alongside them.
    direct = _open_direct(client, owner, other)
    assert direct.status_code == 201, direct.text


# --- the two write paths must agree ---------------------------------------


def test_accepting_an_application_reuses_a_hand_opened_room(client, register_user):
    """The defect that made the fork reachable without any race.

    `POST /chat/rooms` and the accept path each had their own dedup logic. If
    the pair opened a chat by hand and *then* an application was accepted (or the
    reverse), both paths created a room, because each only looked for rooms
    created by itself. They now share one helper and one key.
    """
    owner = register_user(nickname="Organiser")
    applicant = register_user(nickname="Applicant")
    trip = _make_trip(client, owner)

    # The chat is opened by hand first...
    hand_opened = _open_direct(client, owner, applicant)
    assert hand_opened.status_code == 201, hand_opened.text

    # ...then the application is accepted, which also wants a room.
    application = client.post(
        f"/api/v1/trips/{trip['id']}/apply",
        headers=applicant["headers"],
        json={"message": "I would like to join"},
    ).json()
    accepted = client.patch(
        f"/api/v1/trips/applications/{application['id']}",
        headers=owner["headers"],
        params={"decision": "ACCEPTED"},
    )
    assert accepted.status_code == 200, accepted.text

    rooms = _direct_rooms_between(client, owner, applicant)
    assert len(rooms) == 1, f"accept forked the conversation: {[r['id'] for r in rooms]}"
    assert rooms[0]["id"] == hand_opened.json()["id"]


def test_the_accepted_party_sees_the_room_created_by_the_other_side(
    client, register_user
):
    """Both members must be in the room, so it is visible from either account."""
    owner = register_user(nickname="Organiser")
    applicant = register_user(nickname="Applicant")
    trip = _make_trip(client, owner)

    application = client.post(
        f"/api/v1/trips/{trip['id']}/apply",
        headers=applicant["headers"],
        json={"message": "I would like to join"},
    ).json()
    client.patch(
        f"/api/v1/trips/applications/{application['id']}",
        headers=owner["headers"],
        params={"decision": "ACCEPTED"},
    )

    assert len(_direct_rooms_between(client, owner, applicant)) == 1
    assert len(_direct_rooms_between(client, applicant, owner)) == 1


# --- the constraint, not the pre-check, is the guard -----------------------


def test_the_partial_unique_index_exists_and_is_partial(client, raw_db, register_user):
    """Asserted on the schema, because the pre-check alone cannot hold the line.

    See `test_a_direct_room_and_a_trip_group_do_not_collide` for why asserting
    the predicate text is the only way to check it on SQLite — mutating the
    predicate does not change observable behaviour on this engine.
    """
    row = raw_db.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        ("uq_chat_room_direct_pair",),
    ).fetchone()
    assert row is not None, "the partial unique index was never created"
    assert "UNIQUE" in row[0].upper(), row[0]
    assert "direct_pair_key IS NOT NULL" in row[0], row[0]


def test_losing_the_insert_race_returns_the_winners_room(client, register_user, raw_db):
    """The loser must adopt the winner's room, not raise.

    A concurrent race cannot be scheduled deterministically through the HTTP
    client, so the interleaving is simulated: the courtesy `SELECT` is forced to
    see nothing (as it would before the other request committed) while the row
    is already in the table by the time the insert runs. That is exactly the
    state the loser observes.
    """
    import uuid as uuid_module

    from sqlalchemy import select as sa_select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.v1.chat import ensure_direct_room
    from app.core.config import settings
    from app.models.chat import ChatRoom

    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")

    # The winner lands first.
    winner = _open_direct(client, alice, bob)
    assert winner.status_code == 201, winner.text
    winner_id = winner.json()["id"]

    async def scenario() -> str:
        engine = create_async_engine(settings.DATABASE_URL)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as db:
                state = {"blinded": False}
                original = db.execute

                async def execute(stmt, *args, **kwargs):
                    if not state["blinded"] and "FROM chat_rooms" in str(stmt):
                        state["blinded"] = True
                        # Same shape as the real query, guaranteed to match
                        # nothing — this is the stale read the loser performs.
                        return await original(
                            sa_select(ChatRoom).where(ChatRoom.id == uuid_module.uuid4())
                        )
                    return await original(stmt, *args, **kwargs)

                db.execute = execute  # type: ignore[method-assign]
                try:
                    room = await ensure_direct_room(
                        db, uuid_module.UUID(alice["profile"]["id"]),
                        uuid_module.UUID(bob["profile"]["id"]),
                    )
                    return str(room.id)
                finally:
                    db.execute = original  # type: ignore[method-assign]
        finally:
            await engine.dispose()

    adopted = asyncio.run(scenario())
    assert adopted == winner_id, "the loser created a second room instead of adopting"

    # Exactly one room for the pair, and its key is set.
    key_rows = raw_db.execute(
        "SELECT COUNT(*) FROM chat_rooms WHERE direct_pair_key IS NOT NULL "
        "AND direct_pair_key = ?",
        (
            ":".join(
                sorted([alice["profile"]["id"], bob["profile"]["id"]])
            ),
        ),
    ).fetchone()[0]
    assert key_rows == 1, f"expected one keyed room, found {key_rows}"
    assert len(_direct_rooms_between(client, alice, bob)) == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_the_pair_key_is_order_independent(reverse):
    """The key is a pure function of the *set* of participants."""
    import uuid as uuid_module

    from app.models.chat import direct_pair_key

    a, b = uuid_module.uuid4(), uuid_module.uuid4()
    assert direct_pair_key(a, b) == direct_pair_key(b, a)
    if reverse:
        assert direct_pair_key(b, a) == ":".join(sorted([str(a), str(b)]))
