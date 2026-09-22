"""Application-decision state-machine tests.

Accepting or rejecting an application was a plain attribute overwrite with no
check on the current state and no check on capacity. Two consequences, both
reachable by an ordinary client:

1. **A decision was not final.** `PATCH .../applications/{id}?decision=ACCEPTED`
   on an already-REJECTED application returned `200 ACCEPTED`, because the only
   thing between the request and the write was `application.status = decision`.
   A rejected applicant could therefore be silently promoted, and the applicant
   received *both* `APPLICATION_REJECTED` and `APPLICATION_ACCEPTED` for the same
   application — two notifications that contradict each other.

2. **`looking_for_count` was never enforced.** It was validated on input
   (`ge=1, le=20`) and persisted, but no code path read it back, so a creator
   could accept unlimited companions on a trip advertising one.

The existing suite missed both: it did assert a non-creator gets `404`, but that
check returns before the write, so the mutation was never exercised twice.
"""
import pytest


def _make_trip(client, user, **overrides) -> dict:
    payload = {
        "title": "Tokyo cherry blossom trip",
        "description": "Looking for a companion to explore Tokyo in spring.",
        "destination_country": "Japan",
        "destination_city": "Tokyo",
        "budget_type": "MODERATE",
        "target_gender": "ANY",
        "tags": ["PHOTOGRAPHY", "FOOD"],
        "looking_for_count": 2,
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


def _decide(client, application_id, decision, actor) -> object:
    return client.patch(
        f"/api/v1/trips/applications/{application_id}?decision={decision}",
        headers=actor["headers"],
    )


def _notification_types(client, user) -> list[str]:
    payload = client.get("/api/v1/notifications", headers=user["headers"]).json()
    items = payload["items"] if isinstance(payload, dict) and "items" in payload else payload
    return [n["type"] for n in items]


def test_rejected_cannot_be_flipped_to_accepted(client, register_user):
    """A reject must be durable; the applicant must not be told both things."""
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    trip = _make_trip(client, alice)
    aid = _apply(client, trip, bob)["id"]

    first = _decide(client, aid, "REJECTED", alice)
    assert first.status_code == 200
    assert first.json()["status"] == "REJECTED"

    second = _decide(client, aid, "ACCEPTED", alice)
    assert second.status_code == 409, (
        f"re-deciding a settled application must conflict, got {second.status_code}"
    )

    kinds = _notification_types(client, bob)
    assert kinds.count("APPLICATION_REJECTED") == 1, kinds
    assert kinds.count("APPLICATION_ACCEPTED") == 0, (
        f"applicant was told both rejected and accepted: {kinds}"
    )


def test_same_decision_replay_is_idempotent(client, register_user):
    """A retried request must succeed without fanning out a second notification.

    A client that times out and retries has no way to know the first attempt
    landed. Treating that replay as a conflict would turn a successful action
    into a visible error; treating it as a fresh write would double-notify.
    """
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    trip = _make_trip(client, alice)
    aid = _apply(client, trip, bob)["id"]

    first = _decide(client, aid, "ACCEPTED", alice)
    assert first.status_code == 200

    replay = _decide(client, aid, "ACCEPTED", alice)
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "ACCEPTED"

    kinds = _notification_types(client, bob)
    assert kinds.count("APPLICATION_ACCEPTED") == 1, kinds


def test_accept_respects_looking_for_count(client, register_user):
    """The advertised group size is a real limit, and a refused accept changes nothing."""
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, looking_for_count=1)
    applicants = [register_user(nickname=f"Applicant{i}") for i in range(2)]
    aids = [_apply(client, trip, u)["id"] for u in applicants]

    assert _decide(client, aids[0], "ACCEPTED", alice).status_code == 200

    full = _decide(client, aids[1], "ACCEPTED", alice)
    assert full.status_code == 409, f"expected 409 when full, got {full.status_code}"

    detail = client.get(f"/api/v1/trips/{trip['id']}", headers=alice["headers"]).json()
    statuses = {a["id"]: a["status"] for a in detail.get("applications", [])}
    assert statuses.get(aids[1]) == "PENDING", (
        f"a refused accept must not settle the application: {statuses}"
    )
