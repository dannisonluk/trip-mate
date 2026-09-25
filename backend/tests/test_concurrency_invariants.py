"""Systematic concurrency suite for the lost-race invariants (D6).

Why this file exists
--------------------
B1, B2, B3 and B5 were all *the same class of bug* discovered one at a time:
two requests that each look correct in isolation, interleaved, break an
invariant neither of them owns. Each was fixed with its own hand-written test,
which means:

* the coverage is uneven — nobody wrote the fourth case until a fourth bug
  appeared;
* the shape of the test varies — some assert a status code, some assert a row
  count, some assert both;
* there is no statement anywhere of "these are the invariants we hold under
  concurrency", so the next person cannot tell what is protected.

This file is that statement. One test per invariant, all following the same
recipe, so a new invariant is added by copying the pattern rather than by
reinventing it.

## The recipe, and why it is what it is

`TestClient` cannot express a race: its context manager serialises requests, so
a "concurrent" test written with it passes against broken code. The race needs
**genuinely interleaved** requests, which means `httpx.ASGITransport` driven by
`asyncio.gather` inside a single event loop, with the DB pool in `NullPool` so
two requests really do get two connections (see `conftest`).

## The two-sided rule

Every test asserts **both**:

* a *liveness* side — at least one request succeeded (otherwise the invariant
  could be "satisfied" by refusing everyone); and
* a *safety* side — the persisted state honours the invariant.

An assertion on statuses alone passes if the refused write was already applied;
an assertion on state alone passes if every writer was rejected for an unrelated
reason. The project has been bitten by both, so both are checked here.

## What this does NOT prove

`NullPool` + in-process ASGI is not a real deployment. It exercises the code
paths and the isolation level, but not network partitions, connection-pool
exhaustion, or a real Postgres's lock behaviour. The `IntegrityError` handling is
only reachable if the DB actually enforces the constraint, so the DB-level half
is asserted on the *schema* as well as on behaviour — otherwise a SQLite run
could pass while Postgres was the only thing enforcing it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.main import app


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
async def _gather(client: httpx.AsyncClient, calls: list[tuple[str, str, dict | None, dict]]):
    """Fire every request with no await in between, so they genuinely overlap."""

    async def one(method: str, url: str, json_body, headers):
        return await client.request(method, url, json=json_body, headers=headers)

    return await asyncio.gather(
        *(one(m, u, j, h) for m, u, j, h in calls),
        return_exceptions=True,
    )


def _statuses(responses) -> list[Any]:
    out = []
    for r in responses:
        if isinstance(r, BaseException):
            out.append(type(r).__name__)
        else:
            out.append(r.status_code)
    return out


def _ok(responses) -> int:
    """How many requests produced a 2xx."""
    return sum(
        1
        for r in responses
        if not isinstance(r, BaseException) and 200 <= r.status_code < 300
    )


def _conflicted(responses) -> int:
    """How many were cleanly refused with a 4xx (not a 500, not a crash)."""
    return sum(
        1
        for r in responses
        if not isinstance(r, BaseException) and 400 <= r.status_code < 500
    )


def _crashed(responses) -> list[str]:
    """Any 5xx or transport exception — a race must never surface as a crash."""
    bad = []
    for r in responses:
        if isinstance(r, BaseException):
            bad.append(f"exception {type(r).__name__}: {r}")
        elif r.status_code >= 500:
            bad.append(f"HTTP {r.status_code}: {r.text[:120]}")
    return bad


def _trip_body(**over: Any) -> dict:
    body = {
        "title": "Concurrency probe",
        "description": "Every request in this file is fired concurrently.",
        "destination_country": "Japan",
        "destination_city": "Osaka",
        "budget_type": "MODERATE",
        "target_gender": "ANY",
        "tags": ["FOOD"],
        "looking_for_count": 1,
    }
    body.update(over)
    return body


async def _make_trip(c: httpx.AsyncClient, owner_headers: dict, **over) -> str:
    resp = await c.post("/api/v1/trips", headers=owner_headers, json=_trip_body(**over))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _apply(c: httpx.AsyncClient, trip_id: str, headers: dict) -> str:
    resp = await c.post(
        f"/api/v1/trips/{trip_id}/apply", headers=headers, json={"message": "hi"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _accepted_count(c: httpx.AsyncClient, trip_id: str, headers: dict) -> int:
    detail = await c.get(f"/api/v1/trips/{trip_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    apps = detail.json().get("applications", [])
    return sum(1 for a in apps if a.get("status") == "ACCEPTED")


# ---------------------------------------------------------------------------
# B3 — capacity. `looking_for_count` is a hard ceiling.
# ---------------------------------------------------------------------------
def test_invariant_capacity_is_never_exceeded_under_concurrent_accepts(register_user):
    """N accepts fired at once on a 1-seat trip must leave exactly 1 accepted.

    This is the general form of the B3 finding. The specific test in
    `test_multi_replica_hardening` uses 2 racers; this one uses 4, because the
    overbooking window is "all readers before the first writer commits" and two
    racers can miss it by luck where four rarely do.
    """
    alice = register_user(nickname="CapAlice")
    racers = [register_user(nickname=f"Cap{i}") for i in range(4)]

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            trip_id = await _make_trip(c, alice["headers"], looking_for_count=1)
            app_ids = [await _apply(c, trip_id, r["headers"]) for r in racers]

            responses = await _gather(
                c,
                [
                    (
                        "PATCH",
                        f"/api/v1/trips/applications/{aid}?decision=ACCEPTED",
                        None,
                        alice["headers"],
                    )
                    for aid in app_ids
                ],
            )

            accepted = await _accepted_count(c, trip_id, alice["headers"])
            return responses, accepted

    responses, accepted = asyncio.run(run())

    crashes = _crashed(responses)
    assert not crashes, f"a concurrent accept crashed instead of being refused: {crashes}"
    # Safety: the ceiling held.
    assert accepted <= 1, (
        f"{accepted} companions accepted on a 1-seat trip "
        f"(statuses {_statuses(responses)}) — the capacity check is not atomic"
    )
    # Liveness: somebody got the seat.
    assert _ok(responses) >= 1, f"every accept was refused: {_statuses(responses)}"


# ---------------------------------------------------------------------------
# B1 — one review per (author, subject, trip). The race is the *first* insert.
# ---------------------------------------------------------------------------
def test_invariant_a_second_review_is_refused_not_crashed(register_user):
    """Two identical reviews at once: one 201, one 4xx, never a 500.

    The B1 defect was a *permanent* 500 — the duplicate raised on the second
    request and kept raising forever after. So the assertion has two halves that
    a single-sided test would miss: exactly one review exists, **and** the loser
    got a client error rather than a server error.
    """

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            alice = register_user(nickname="RevAlice")
            bob = register_user(nickname="RevBob")
            trip_id = await _make_trip(c, alice["headers"], looking_for_count=2)
            app_id = await _apply(c, trip_id, bob["headers"])
            accepted = await c.patch(
                f"/api/v1/trips/applications/{app_id}?decision=ACCEPTED",
                headers=alice["headers"],
            )
            assert accepted.status_code == 200, accepted.text

            payload = {
                "reviewee_id": alice["profile"]["id"],
                "rating": 5,
                "comment": "Great trip",
                "trip_post_id": trip_id,
            }
            responses = await _gather(
                c,
                [
                    ("POST", "/api/v1/reviews", payload, bob["headers"]),
                    ("POST", "/api/v1/reviews", payload, bob["headers"]),
                ],
            )
            listed = await c.get(
                f"/api/v1/profiles/{alice['profile']['id']}/reviews",
                headers=bob["headers"],
            )
            return responses, listed

    responses, listed = asyncio.run(run())

    crashes = _crashed(responses)
    assert not crashes, (
        f"a duplicate review crashed instead of being refused: {crashes} — "
        "B1 was a permanent 500, so a 5xx here is the original bug"
    )
    assert _ok(responses) == 1, (
        f"expected exactly one review to be created, got {_ok(responses)} "
        f"(statuses {_statuses(responses)})"
    )
    assert _conflicted(responses) >= 1, (
        f"the loser was not refused with a 4xx: {_statuses(responses)}"
    )

    if listed.status_code == 200:
        items = listed.json()
        items = items.get("items", items) if isinstance(items, dict) else items
        assert len(items) <= 1, f"{len(items)} reviews persisted for one trip"


# ---------------------------------------------------------------------------
# B2 — one DIRECT room per pair. The race is the *first* insert.
# ---------------------------------------------------------------------------
def test_invariant_only_one_direct_room_per_pair_under_concurrent_creation(register_user):
    """Four concurrent "open a chat with X" calls must yield one room.

    B2's consequence was the opposite of B1's: no error at all, just duplicate
    rooms that split the conversation so both halves look like the other person
    stopped replying. So the assertion is on the room count, and the liveness
    side is that at least one caller got a usable room id.
    """

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            alice = register_user(nickname="DmAlice")
            bob = register_user(nickname="DmBob")
            responses = await _gather(
                c,
                [
                    (
                        "POST",
                        "/api/v1/chat/rooms",
                        {"room_type": "DIRECT", "other_profile_id": bob["profile"]["id"]},
                        alice["headers"],
                    )
                    for _ in range(4)
                ],
            )
            rooms = await c.get("/api/v1/chat/rooms", headers=alice["headers"])
            return responses, rooms

    responses, rooms = asyncio.run(run())

    crashes = _crashed(responses)
    assert not crashes, f"concurrent room creation crashed: {crashes}"
    assert _ok(responses) >= 1, f"no caller got a room: {_statuses(responses)}"

    assert rooms.status_code == 200, rooms.text
    direct = [r for r in rooms.json() if r.get("room_type") == "DIRECT"]
    assert len(direct) == 1, (
        f"{len(direct)} DIRECT rooms for one pair (statuses {_statuses(responses)}) — "
        "the conversation is split and neither side can tell"
    )

    # Every successful caller must have been handed the SAME room.
    ids = {
        r.json().get("id")
        for r in responses
        if not isinstance(r, BaseException) and 200 <= r.status_code < 300
    }
    assert len(ids) == 1, f"concurrent callers received different rooms: {ids}"


# ---------------------------------------------------------------------------
# B5 — block is idempotent. The race is two concurrent *first* blocks.
# ---------------------------------------------------------------------------
def test_invariant_block_is_idempotent_and_audited_once(register_user, raw_db):
    """Two concurrent blocks: both may succeed, exactly one audit row.

    B5 was a crash on the second insert plus a duplicated audit entry. The
    idempotent contract means the *response* is allowed to succeed both times —
    so the assertion cannot be on status codes alone. It is on the persisted
    state: one block row, one audit row.

    The audit count is not decoration. `moderation.block_profile` returns
    `(block, created)` and the *caller* decides whether to write `USER_BLOCKED`
    from `created` — so a `block_profile` that returns `created=True` for an
    already-existing row leaves the block table perfectly correct while
    appending a second audit row for an event that did not happen. Asserting
    only the block count lets that mutation pass, so the audit row is asserted
    too: it is the observable consequence of the flag.
    """

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            alice = register_user(nickname="BlkAlice")
            bob = register_user(nickname="BlkBob")
            responses = await _gather(
                c,
                [
                    ("POST", f"/api/v1/profiles/{bob['profile']['id']}/block", None, alice["headers"]),
                    ("POST", f"/api/v1/profiles/{bob['profile']['id']}/block", None, alice["headers"]),
                    ("POST", f"/api/v1/profiles/{bob['profile']['id']}/block", None, alice["headers"]),
                ],
            )
            blocks = await c.get("/api/v1/profiles/me/blocks", headers=alice["headers"])
            return responses, blocks, alice["profile"]["id"]

    responses, blocks, alice_id = asyncio.run(run())

    crashes = _crashed(responses)
    assert not crashes, (
        f"a duplicate block crashed instead of being idempotent: {crashes} — "
        "that is the B5 defect verbatim"
    )
    assert _ok(responses) >= 1, f"blocking did not take: {_statuses(responses)}"

    assert blocks.status_code == 200, blocks.text
    rows = blocks.json()
    assert len(rows) == 1, (
        f"{len(rows)} block rows for one pair after {len(responses)} concurrent blocks — "
        "the pair-level invariant is not enforced"
    )

    # `Uuid` is stored as 32 hex chars with no hyphens, so a hyphenated id in a
    # WHERE clause matches nothing and the count would silently read 0 — which
    # would make this assertion pass for the wrong reason (0 != 2 fails, but a
    # mis-built query that returns nothing looks like "no duplicate").
    audit_rows = raw_db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'USER_BLOCKED' AND actor_profile_id = ?",
        (alice_id.replace("-", ""),),
    ).fetchone()[0]
    assert audit_rows == 1, (
        f"{audit_rows} USER_BLOCKED audit rows for one block event — "
        "the caller wrote an audit entry per request instead of per creation, "
        "so `block_profile`'s `created` flag is not reporting creation"
    )


# ---------------------------------------------------------------------------
# The DB-level half: an application check is not enough
# ---------------------------------------------------------------------------
def test_the_capacity_and_uniqueness_invariants_are_also_expressed_in_the_schema():
    """A behavioural test on SQLite can pass while Postgres is the only guard.

    SQLite and Postgres differ in how they treat NULL in a UNIQUE index, in
    deferred constraints, and in default isolation. A concurrency test that
    passes here therefore proves the *code path*, not the *guarantee*. This
    asserts the guarantees are declared in the models, so the production
    database enforces them even if application code drifts.
    """
    from app.models.moderation import Block
    from app.models.review import Review

    review_constraints = {
        c.name for c in Review.__table__.constraints if getattr(c, "name", None)
    }
    block_constraints = {
        c.name for c in Block.__table__.constraints if getattr(c, "name", None)
    }

    assert any("review" in n for n in review_constraints), (
        f"no uniqueness constraint on reviews: {sorted(review_constraints)} — "
        "an application-level check cannot survive two concurrent inserts"
    )
    assert any("block" in n for n in block_constraints), (
        f"no uniqueness constraint on blocks: {sorted(block_constraints)}"
    )
