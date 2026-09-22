"""Review-authorization regression tests.

The review system exists to stop score-bombing: you may rate only someone you
actually travelled with, and only once per trip. Both halves of that sentence are
enforced by different mechanisms, and the gap between them is where these tests
live.

The original bug: the travel check answered a *pair-level* question ("have these
two ever travelled together?") while the duplicate guard was scoped per
`(reviewer, reviewee, trip_post_id)`. `trip_post_id` was client-supplied and never
validated, so one genuine shared trip was a licence to post unlimited reviews by
varying the trip id on each request — each one attached to a trip neither party
had been on. The existing test missed it because it always reused the *same*
`trip_post_id`, which the duplicate guard does catch.
"""
import pytest


def _make_trip(client, user, **overrides) -> dict:
    payload = {
        "title": "Kyoto autumn trip",
        "description": "Looking for a companion to walk Kyoto in autumn.",
        "destination_country": "Japan",
        "destination_city": "Kyoto",
        "budget_type": "MODERATE",
        "target_gender": "ANY",
        "tags": ["PHOTOGRAPHY"],
        "looking_for_count": 2,
    }
    payload.update(overrides)
    resp = client.post("/api/v1/trips", headers=user["headers"], json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _apply_and_decide(client, trip, applicant, owner, decision: str) -> dict:
    """Submit an application and have the owner accept or reject it."""
    application = client.post(
        f"/api/v1/trips/{trip['id']}/apply",
        headers=applicant["headers"],
        json={"message": "I would love to join."},
    ).json()
    resp = client.patch(
        f"/api/v1/trips/applications/{application['id']}?decision={decision}",
        headers=owner["headers"],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _review(client, reviewer, reviewee, **body):
    payload = {"reviewee_id": reviewee["profile"]["id"], "rating": 5}
    payload.update(body)
    return client.post("/api/v1/reviews", headers=reviewer["headers"], json=payload)


def _summary(client, viewer, target) -> dict:
    resp = client.get(
        f"/api/v1/profiles/{target['profile']['id']}/reviews/summary",
        headers=viewer["headers"],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- the trip id must be one you both actually took part in ----------------


def test_review_must_name_a_trip_both_parties_shared(client, register_user):
    """A shared trip somewhere else does not license a review of *this* trip."""
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    shared = _make_trip(client, alice)
    _apply_and_decide(client, shared, bob, alice, "ACCEPTED")

    # Bob owns a trip Alice was never on.
    bobs_own = _make_trip(client, bob)

    resp = _review(client, bob, alice, trip_post_id=bobs_own["id"], rating=1)
    assert resp.status_code == 403, resp.text
    assert _summary(client, alice, alice)["count"] == 0


def test_review_cannot_be_replayed_against_other_trip_ids(client, register_user):
    """The score-bombing scenario, end to end.

    One accepted application, then as many reviews as the attacker can invent
    trip ids for. Before the fix every one of these returned 201.
    """
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    shared = _make_trip(client, alice)
    _apply_and_decide(client, shared, bob, alice, "ACCEPTED")

    legit = _review(client, bob, alice, trip_post_id=shared["id"], rating=5)
    assert legit.status_code == 201, legit.text

    # Alice's other trip, which Bob never applied to.
    alices_other = _make_trip(client, alice)
    # Bob's own trip.
    bobs_own = _make_trip(client, bob)

    for trip_id in (alices_other["id"], bobs_own["id"], shared["id"]):
        resp = _review(client, bob, alice, trip_post_id=trip_id, rating=1)
        assert resp.status_code in (403, 409), (trip_id, resp.status_code, resp.text)

    # The decisive assertion: the rating is still the one legitimate review.
    summary = _summary(client, alice, alice)
    assert summary["count"] == 1, summary
    assert summary["average_rating"] == 5.0, summary


def test_a_rejected_application_does_not_unlock_reviewing(client, register_user):
    """Only ACCEPTED counts. A rejected applicant never travelled with anyone."""
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    trip = _make_trip(client, alice)
    _apply_and_decide(client, trip, bob, alice, "REJECTED")

    assert _review(client, bob, alice, trip_post_id=trip["id"]).status_code == 403
    assert _review(client, bob, alice).status_code == 403


def test_a_pending_application_does_not_unlock_reviewing(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    trip = _make_trip(client, alice)
    client.post(
        f"/api/v1/trips/{trip['id']}/apply", headers=bob["headers"], json={"message": "Hi"}
    )
    assert _review(client, bob, alice, trip_post_id=trip["id"]).status_code == 403


# --- the positive cases the fix must NOT break -----------------------------


def test_two_shared_trips_allow_one_review_each(client, register_user):
    """`uq_review_once_per_trip` means exactly what it says: once *per trip*.

    Travelling together twice is two trips, so it is two reviews. A fix that
    collapsed this to one review per pair would be over-restrictive.
    """
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    first = _make_trip(client, alice)
    second = _make_trip(client, alice)
    _apply_and_decide(client, first, bob, alice, "ACCEPTED")
    _apply_and_decide(client, second, bob, alice, "ACCEPTED")

    assert _review(client, bob, alice, trip_post_id=first["id"], rating=4).status_code == 201
    assert _review(client, bob, alice, trip_post_id=second["id"], rating=5).status_code == 201
    # ...but not twice for the same trip.
    assert _review(client, bob, alice, trip_post_id=first["id"], rating=1).status_code == 409

    summary = _summary(client, alice, alice)
    assert summary["count"] == 2
    assert summary["average_rating"] == 4.5


def test_review_without_a_trip_id_is_still_once_only(client, register_user):
    """The NULL case the database constraint cannot cover.

    SQL treats NULLs as distinct, so `uq_review_once_per_trip` does not stop a
    second row with `trip_post_id IS NULL`. The application check is the only
    guard, which is why it is spelled out as `IS NULL` rather than `==`.
    """
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    trip = _make_trip(client, alice)
    _apply_and_decide(client, trip, bob, alice, "ACCEPTED")

    assert _review(client, bob, alice, rating=4).status_code == 201
    assert _review(client, bob, alice, rating=1).status_code == 409
    assert _summary(client, alice, alice)["count"] == 1


# --- reviews are part of the profile surface, so a block hides them ---------


def test_a_block_hides_reviews_from_the_blocked_user(client, register_user):
    """404, not 403 — otherwise the response itself confirms the blocker exists."""
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    trip = _make_trip(client, alice)
    _apply_and_decide(client, trip, bob, alice, "ACCEPTED")
    assert _review(client, bob, alice, trip_post_id=trip["id"], rating=5).status_code == 201

    # Visible while there is no block...
    assert client.get(
        f"/api/v1/profiles/{alice['profile']['id']}/reviews", headers=bob["headers"]
    ).status_code == 200

    assert client.post(
        f"/api/v1/profiles/{bob['profile']['id']}/block", headers=alice["headers"]
    ).status_code == 201

    # ...hidden afterwards, on both review endpoints, with the same 404 the
    # profile endpoint itself returns.
    for path in (
        f"/api/v1/profiles/{alice['profile']['id']}/reviews",
        f"/api/v1/profiles/{alice['profile']['id']}/reviews/summary",
    ):
        resp = client.get(path, headers=bob["headers"])
        assert resp.status_code == 404, (path, resp.status_code)


def test_a_block_hides_reviews_in_both_directions(client, register_user):
    """Blocks are stored directionally but enforced symmetrically."""
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    trip = _make_trip(client, bob)
    _apply_and_decide(client, trip, alice, bob, "ACCEPTED")

    assert client.post(
        f"/api/v1/profiles/{alice['profile']['id']}/block", headers=bob["headers"]
    ).status_code == 201

    resp = client.get(
        f"/api/v1/profiles/{bob['profile']['id']}/reviews", headers=alice["headers"]
    )
    assert resp.status_code == 404


def test_review_reads_accept_a_user_id_as_well_as_a_profile_id(client, register_user):
    """`/reviews` and `/reviews/summary` share a path prefix, so they must agree
    on what the id means. The summary endpoint previously accepted only a profile
    id while its sibling also resolved a user id."""
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    carla = register_user(nickname="Carla")
    trip = _make_trip(client, alice)
    _apply_and_decide(client, trip, bob, alice, "ACCEPTED")
    assert _review(client, bob, alice, trip_post_id=trip["id"], rating=5).status_code == 201

    by_profile_id = client.get(
        f"/api/v1/profiles/{alice['profile']['id']}/reviews/summary", headers=carla["headers"]
    )
    by_user_id = client.get(
        f"/api/v1/profiles/{alice['profile']['user_id']}/reviews/summary",
        headers=carla["headers"],
    )
    assert by_profile_id.status_code == 200, by_profile_id.text
    assert by_user_id.status_code == 200, by_user_id.text
    assert by_profile_id.json() == by_user_id.json()
    assert by_user_id.json()["count"] == 1


def test_unknown_profile_id_is_404_on_both_review_reads(client, register_user):
    import uuid

    viewer = register_user()
    missing = uuid.uuid4()
    for path in (
        f"/api/v1/profiles/{missing}/reviews",
        f"/api/v1/profiles/{missing}/reviews/summary",
    ):
        assert client.get(path, headers=viewer["headers"]).status_code == 404
