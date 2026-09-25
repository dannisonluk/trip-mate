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


# --- the same invariant has a second writer: PATCH /trips/{id} (B3) --------


def _patch_trip(client, trip, actor, **changes):
    return client.patch(
        f"/api/v1/trips/{trip['id']}", headers=actor["headers"], json=changes
    )


def _accepted_ids(client, trip, actor) -> set[str]:
    detail = client.get(f"/api/v1/trips/{trip['id']}", headers=actor["headers"]).json()
    return {a["id"] for a in detail.get("applications", []) if a["status"] == "ACCEPTED"}


def test_lowering_looking_for_count_below_accepted_is_refused(client, register_user):
    """The defect: capacity was enforced only on the accept path.

    `looking_for_count` is a plain field on `TripPostUpdate`, so an organiser
    could accept two companions and then advertise a group of one. The write
    returned `200 OK` and the trip was left overbooked — and *unrepairable*,
    because the only remaining move was to raise the limit again, which does not
    un-accept anyone. The overbooking was invisible to every existing test.
    """
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, looking_for_count=3)
    applicants = [register_user(nickname=f"Applicant{i}") for i in range(3)]
    aids = [_apply(client, trip, u)["id"] for u in applicants]

    assert _decide(client, aids[0], "ACCEPTED", alice).status_code == 200
    assert _decide(client, aids[1], "ACCEPTED", alice).status_code == 200

    resp = _patch_trip(client, trip, alice, looking_for_count=1)
    assert resp.status_code == 409, resp.text

    # The decisive assertion: the trip is *not* overbooked. The 409 alone could
    # be raised before the write and still leave the original value — but the
    # thing that must hold is that accepted never exceeds the limit.
    assert len(_accepted_ids(client, trip, alice)) == 2
    after = client.get(f"/api/v1/trips/{trip['id']}", headers=alice["headers"]).json()
    assert after["looking_for_count"] == 3, (
        f"a refused shrink must not change the limit: {after['looking_for_count']}"
    )


def test_lowering_looking_for_count_onto_the_accepted_count_is_allowed(
    client, register_user
):
    """The boundary that a naive `>` would wrongly reject.

    With 2 companions accepted, `3 -> 2` is exactly full, not overbooked. The
    capacity helper has to know whether the caller is *about to add* an accepted
    row (the accept path) or is merely changing the limit (this path), because
    the two need different arithmetic on the same invariant. This test is what
    distinguishes them.
    """
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, looking_for_count=3)
    applicants = [register_user(nickname=f"Applicant{i}") for i in range(3)]
    aids = [_apply(client, trip, u)["id"] for u in applicants]

    assert _decide(client, aids[0], "ACCEPTED", alice).status_code == 200
    assert _decide(client, aids[1], "ACCEPTED", alice).status_code == 200

    resp = _patch_trip(client, trip, alice, looking_for_count=2)
    assert resp.status_code == 200, resp.text
    assert resp.json()["looking_for_count"] == 2

    # ...and one step further is refused.
    assert _patch_trip(client, trip, alice, looking_for_count=1).status_code == 409


def test_raising_looking_for_count_uses_the_patch_path_not_the_accept_path(
    client, register_user
):
    """Guard for the shared helper's arithmetic.

    The two callers need different numbers: the accept path compares `count + 1`
    (the application is still PENDING), while this path compares `count` alone.
    A raise on a trip whose count already fills the *old* limit must still be
    allowed — if the helper applied the accept-path formula here it would read
    `1 + 1 > 1` and refuse a perfectly legal increase.
    """
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, looking_for_count=1)
    applicant = register_user(nickname="Applicant")
    aid = _apply(client, trip, applicant)["id"]
    assert _decide(client, aid, "ACCEPTED", alice).status_code == 200

    resp = _patch_trip(client, trip, alice, looking_for_count=5)
    assert resp.status_code == 200, resp.text
    assert resp.json()["looking_for_count"] == 5


def test_patching_unrelated_fields_does_not_run_the_capacity_check(
    client, register_user
):
    """Only `looking_for_count` is capacity-relevant; an edit must not be blocked
    by a trip that happens to be full."""
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, looking_for_count=1)
    applicant = register_user(nickname="Applicant")
    aid = _apply(client, trip, applicant)["id"]
    assert _decide(client, aid, "ACCEPTED", alice).status_code == 200

    resp = _patch_trip(client, trip, alice, description="Updated description here.")
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "Updated description here."


# --- `status` is a lifecycle, not a free-form field ------------------------


def test_a_cancelled_trip_cannot_be_revived(client, register_user):
    """`CANCELLED` is terminal — otherwise a post applicants had written off
    comes back with one PATCH and they are silently re-enlisted."""
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice)

    assert _patch_trip(client, trip, alice, status="CANCELLED").status_code == 200
    resp = _patch_trip(client, trip, alice, status="OPEN")
    assert resp.status_code == 409, resp.text

    after = client.get(f"/api/v1/trips/{trip['id']}", headers=alice["headers"]).json()
    assert after["status"] == "CANCELLED", after["status"]


def test_closed_and_open_may_alternate(client, register_user):
    """`CLOSED` is the normal "we are full" state, not a terminal one."""
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice)

    assert _patch_trip(client, trip, alice, status="CLOSED").status_code == 200
    assert _patch_trip(client, trip, alice, status="OPEN").status_code == 200
    assert _patch_trip(client, trip, alice, status="CANCELLED").status_code == 200


def test_resending_the_current_status_is_a_noop_not_a_conflict(client, register_user):
    """A client that PATCHes a whole form must not get a 409 for a field it did
    not intend to move."""
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, status="CANCELLED")
    assert _patch_trip(client, trip, alice, status="CANCELLED").status_code == 200


# --- an explicit null is a 422, not an IntegrityError ----------------------


@pytest.mark.parametrize(
    "field",
    ["title", "description", "destination_country", "budget_type", "target_gender", "tags"],
)
def test_explicit_null_on_a_non_nullable_field_is_422(client, register_user, field):
    """`null` used to mean "set this column to NULL".

    Every field on the update schema is optional so the patch can be partial,
    which makes `None` do double duty as "absent". `exclude_unset=True` covers
    the absent case, so an explicit null reached `setattr(post, field, None)`
    and the NOT NULL column raised an `IntegrityError` — a 500 for a malformed
    request. The schema now rejects it up front.
    """
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice)

    resp = _patch_trip(client, trip, alice, **{field: None})
    assert resp.status_code == 422, (field, resp.status_code, resp.text)

    # The trip is unchanged: a rejected request must not have half-applied.
    after = client.get(f"/api/v1/trips/{trip['id']}", headers=alice["headers"]).json()
    assert after["title"] == trip["title"]
    assert after["status"] == "OPEN"


def test_explicit_null_on_a_genuinely_nullable_field_is_allowed(client, register_user):
    """The other half of the rule: `end_date` has nothing to be nulled *from*."""
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, start_date="2026-12-01", end_date="2026-12-10")

    resp = _patch_trip(client, trip, alice, end_date=None)
    assert resp.status_code == 200, resp.text
    assert resp.json()["end_date"] is None


def test_an_already_overbooked_trip_cannot_be_made_worse(client, register_user):
    """A trip damaged before this guard existed must not be damageable further.

    The pathological state is real: an organiser who exploited the old bug can
    hold `looking_for_count=1` with three accepted companions. The invariant is
    not "the limit may never change" — it is "the limit must never sit below
    the accepted count", so every write that would deepen the violation is
    refused while a correction back towards sanity is not.
    """
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, looking_for_count=3)
    applicants = [register_user(nickname=f"Applicant{i}") for i in range(3)]
    aids = [_apply(client, trip, u)["id"] for u in applicants]
    for aid in aids:
        assert _decide(client, aid, "ACCEPTED", alice).status_code == 200

    # Force the overbooked legacy state through a path this guard does not cover
    # — the schema validates the value on *input*, so it has to be written
    # directly, exactly as the pre-fix endpoint would have.
    from tests.conftest import test_db_path
    import sqlite3

    con = sqlite3.connect(test_db_path())
    try:
        con.execute(
            "UPDATE trip_posts SET looking_for_count = 1 WHERE id = ?",
            (trip["id"].replace("-", ""),),
        )
        con.commit()
    finally:
        con.close()

    # The violation is now visible, and every capacity write is refused.
    assert _patch_trip(client, trip, alice, looking_for_count=1).status_code == 409
    assert _patch_trip(client, trip, alice, looking_for_count=2).status_code == 409

    # But the limit can be raised back above the accepted count, which is the
    # one move that repairs it. A guard that refused every limit change on an
    # overbooked trip would lock the organiser out of fixing their own mistake.
    resp = _patch_trip(client, trip, alice, looking_for_count=3)
    assert resp.status_code == 200, resp.text
    assert resp.json()["looking_for_count"] == 3


