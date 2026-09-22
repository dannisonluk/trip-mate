"""TRIP group rooms.

The group room is the only multi-party surface in the product, so the
membership rules matter:

* only the organiser may open it (otherwise anyone could attach a room to
  someone else's trip and seed themselves in);
* membership is derived from ACCEPTED applications — a "group" containing just
  the organiser would be a no-op feature;
* opening it twice must not fork the conversation into two rooms.
"""
from __future__ import annotations

from tests.test_api_flow import _make_trip


def _create_group(client, user, trip_id, **extra):
    return client.post(
        "/api/v1/chat/rooms",
        headers=user["headers"],
        json={"room_type": "TRIP", "trip_post_id": trip_id, **extra},
    )


def _apply(client, applicant, trip_id):
    resp = client.post(
        f"/api/v1/trips/{trip_id}/apply",
        headers=applicant["headers"],
        json={"message": "I would like to join"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _accept(client, owner, application_id):
    resp = client.patch(
        f"/api/v1/trips/applications/{application_id}",
        headers=owner["headers"],
        params={"decision": "ACCEPTED"},
    )
    assert resp.status_code == 200, resp.text


def test_group_includes_organiser_and_accepted_applicants(client, register_user):
    owner = register_user(nickname="Organiser")
    accepted = register_user(nickname="Accepted")
    pending = register_user(nickname="StillWaiting")
    trip = _make_trip(client, owner)

    app_accepted = _apply(client, accepted, trip["id"])
    _apply(client, pending, trip["id"])
    _accept(client, owner, app_accepted["id"])

    resp = _create_group(client, owner, trip["id"])
    assert resp.status_code == 201, resp.text
    room = resp.json()

    member_ids = {m["profile_id"] for m in room["members"]}
    assert owner["profile"]["id"] in member_ids
    assert accepted["profile"]["id"] in member_ids
    # A PENDING applicant has not been accepted and must not be in the group.
    assert pending["profile"]["id"] not in member_ids, "pending applicant was added to the group"
    assert room["room_type"] == "TRIP"
    # Title falls back to the trip title so the chat list is readable.
    assert room["title"] == trip["title"]


def test_group_is_visible_to_accepted_member(client, register_user):
    owner = register_user(nickname="Organiser")
    accepted = register_user(nickname="Accepted")
    trip = _make_trip(client, owner)
    app = _apply(client, accepted, trip["id"])
    _accept(client, owner, app["id"])

    room = _create_group(client, owner, trip["id"]).json()

    rooms = client.get("/api/v1/chat/rooms", headers=accepted["headers"]).json()
    assert room["id"] in {r["id"] for r in rooms}, "accepted member cannot see the group room"


def test_only_organiser_can_open_group(client, register_user):
    owner = register_user(nickname="Organiser")
    outsider = register_user(nickname="Outsider")
    trip = _make_trip(client, owner)

    resp = _create_group(client, outsider, trip["id"])
    assert resp.status_code == 403, "a non-organiser was able to open the group"

    # ...and nothing was created.
    rooms = client.get("/api/v1/chat/rooms", headers=outsider["headers"]).json()
    assert not [r for r in rooms if r["room_type"] == "TRIP"]


def test_opening_group_twice_reuses_the_same_room(client, register_user):
    owner = register_user(nickname="Organiser")
    accepted = register_user(nickname="Accepted")
    trip = _make_trip(client, owner)
    app = _apply(client, accepted, trip["id"])
    _accept(client, owner, app["id"])

    first = _create_group(client, owner, trip["id"]).json()
    second = _create_group(client, owner, trip["id"]).json()
    assert first["id"] == second["id"], "a second group room was created for the same trip"

    rooms = client.get("/api/v1/chat/rooms", headers=owner["headers"]).json()
    trip_rooms = [r for r in rooms if r["room_type"] == "TRIP" and r["trip_post_id"] == trip["id"]]
    assert len(trip_rooms) == 1


def test_group_requires_existing_trip(client, register_user):
    owner = register_user(nickname="Organiser")
    resp = _create_group(client, owner, "00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_group_requires_trip_id(client, register_user):
    owner = register_user(nickname="Organiser")
    resp = client.post(
        "/api/v1/chat/rooms", headers=owner["headers"], json={"room_type": "TRIP"}
    )
    assert resp.status_code == 400
