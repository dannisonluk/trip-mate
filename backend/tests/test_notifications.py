"""Notification system: fan-out, block-awareness, and access control.

The security-relevant properties here are:

* A block must suppress notifications in both directions. If it did not, the
  notification bell becomes a harassment channel that bypasses the block.
* Notifications are never readable by anyone but the recipient — enforced in the
  WHERE clause so a foreign id yields 404, not 403 (no existence leak).
* No self-notifications.
"""
from __future__ import annotations

from tests.test_api_flow import _make_trip, _recv_until, _token


def _notifications(client, user, **params) -> dict:
    resp = client.get("/api/v1/notifications", headers=user["headers"], params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _unread(client, user) -> int:
    resp = client.get("/api/v1/notifications/unread-count", headers=user["headers"])
    assert resp.status_code == 200, resp.text
    return resp.json()["unread"]


def _apply(client, applicant, trip_id, message="Let's go!"):
    resp = client.post(
        f"/api/v1/trips/{trip_id}/apply",
        headers=applicant["headers"],
        json={"message": message},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _decide(client, owner, application_id, decision):
    resp = client.patch(
        f"/api/v1/trips/applications/{application_id}",
        headers=owner["headers"],
        params={"decision": decision},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------
# Application lifecycle
# --------------------------------------------------------------------------

def test_application_notifies_trip_owner(client, register_user):
    owner = register_user(nickname="Owner")
    applicant = register_user(nickname="Applicant")
    trip = _make_trip(client, owner)

    assert _unread(client, owner) == 0
    _apply(client, applicant, trip["id"])

    page = _notifications(client, owner)
    assert page["unread"] == 1
    item = page["items"][0]
    assert item["type"] == "APPLICATION_RECEIVED"
    assert "Applicant" in item["title"]
    assert item["actor"]["nickname"] == "Applicant"
    assert item["trip_post_id"] == trip["id"]

    # The applicant must not be notified about their own action.
    assert _unread(client, applicant) == 0


def test_acceptance_notifies_applicant(client, register_user):
    owner = register_user(nickname="Owner")
    applicant = register_user(nickname="Applicant")
    trip = _make_trip(client, owner)
    application = _apply(client, applicant, trip["id"])

    _decide(client, owner, application["id"], "ACCEPTED")

    page = _notifications(client, applicant)
    assert page["unread"] == 1
    assert page["items"][0]["type"] == "APPLICATION_ACCEPTED"
    assert "Owner" in page["items"][0]["title"]


def test_rejection_notifies_applicant(client, register_user):
    owner = register_user(nickname="Owner")
    applicant = register_user(nickname="Applicant")
    trip = _make_trip(client, owner)
    application = _apply(client, applicant, trip["id"])

    _decide(client, owner, application["id"], "REJECTED")

    page = _notifications(client, applicant)
    assert page["items"][0]["type"] == "APPLICATION_REJECTED"


# --------------------------------------------------------------------------
# Block awareness — the harassment-vector guard
# --------------------------------------------------------------------------

def test_blocked_user_cannot_notify(client, register_user):
    """B applies to A's trip, then A blocks B. Further applications must not notify.

    Blocking must silence the notification channel, otherwise a blocked user can
    still make the victim's phone buzz.
    """
    owner = register_user(nickname="Owner")
    other = register_user(nickname="Other")
    trip = _make_trip(client, owner)

    # First application succeeds and notifies.
    _apply(client, other, trip["id"])
    assert _unread(client, owner) == 1

    # Owner blocks the applicant.
    blocked_profile_id = other["profile"]["id"]
    resp = client.post(
        f"/api/v1/profiles/{blocked_profile_id}/block", headers=owner["headers"]
    )
    assert resp.status_code == 201, resp.text

    # Mark existing ones read so we only measure new activity.
    client.post("/api/v1/notifications/read-all", headers=owner["headers"])
    assert _unread(client, owner) == 0

    # The blocked user applies to a second trip — must be refused outright.
    second = _make_trip(client, owner, title="Second trip to Osaka")
    resp = client.post(
        f"/api/v1/trips/{second['id']}/apply",
        headers=other["headers"],
        json={"message": "hi"},
    )
    assert resp.status_code == 403
    assert _unread(client, owner) == 0, "a blocked user still reached the notification bell"


# --------------------------------------------------------------------------
# Chat messages
# --------------------------------------------------------------------------

def test_chat_message_notifies_other_member(client, register_user):
    owner = register_user(nickname="Owner")
    applicant = register_user(nickname="Applicant")
    trip = _make_trip(client, owner)
    application = _apply(client, applicant, trip["id"])
    _decide(client, owner, application["id"], "ACCEPTED")
    # Clear the application-lifecycle notifications so the assertion below
    # measures only the chat message.
    client.post("/api/v1/notifications/read-all", headers=applicant["headers"])
    client.post("/api/v1/notifications/read-all", headers=owner["headers"])

    rooms = client.get("/api/v1/chat/rooms", headers=applicant["headers"]).json()
    room_id = rooms[0]["id"]

    with client.websocket_connect(
        f"/api/v1/ws/chat/{room_id}?token={_token(applicant['headers'])}"
    ) as ws:
        _recv_until(ws, "presence")
        ws.send_json({"type": "message", "content": "Hello there"})
        _recv_until(ws, "message")

    page = _notifications(client, owner)
    assert page["unread"] == 1
    item = page["items"][0]
    assert item["type"] == "NEW_MESSAGE"
    assert item["chat_room_id"] == room_id
    assert item["body"] == "Hello there"
    # The sender must not be notified about their own message.
    assert _unread(client, applicant) == 0


def test_repeated_messages_upsert_into_one_badge(client, register_user):
    """A busy room yields ONE unread notification, not one per message.

    Otherwise the badge counts message volume rather than "rooms needing
    attention", and a chatty peer can bury every other notification.

    Sends exactly TWO messages: the WS token bucket allows 2 msg/s, and a third
    rapid message returns `{"type": "error", "code": "rate_limited"}` instead of
    a `message` frame — which would leave the receive helper waiting forever.
    Two is sufficient to distinguish "upsert" from "one row per message".
    """
    owner = register_user(nickname="Owner")
    applicant = register_user(nickname="Applicant")
    trip = _make_trip(client, owner)
    application = _apply(client, applicant, trip["id"])
    _decide(client, owner, application["id"], "ACCEPTED")
    client.post("/api/v1/notifications/read-all", headers=owner["headers"])

    rooms = client.get("/api/v1/chat/rooms", headers=applicant["headers"]).json()
    room_id = rooms[0]["id"]

    with client.websocket_connect(
        f"/api/v1/ws/chat/{room_id}?token={_token(applicant['headers'])}"
    ) as ws:
        _recv_until(ws, "presence")
        for text in ("one", "two"):
            ws.send_json({"type": "message", "content": text})
            _recv_until(ws, "message")

    page = _notifications(client, owner)
    assert page["unread"] == 1, f"expected one badge, got {page['unread']}"
    # The preview should reflect the most recent message.
    assert page["items"][0]["body"] == "two"


# --------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------

def test_cannot_read_another_users_notification(client, register_user):
    owner = register_user(nickname="Owner")
    applicant = register_user(nickname="Applicant")
    trip = _make_trip(client, owner)
    _apply(client, applicant, trip["id"])

    victim_notification = _notifications(client, owner)["items"][0]

    # The applicant tries to mark the owner's notification as read.
    resp = client.post(
        f"/api/v1/notifications/{victim_notification['id']}/read",
        headers=applicant["headers"],
    )
    assert resp.status_code == 404, "cross-user access must be indistinguishable from missing"

    # ...and the owner's notification is still unread.
    assert _unread(client, owner) == 1


def test_cannot_delete_another_users_notification(client, register_user):
    owner = register_user(nickname="Owner")
    applicant = register_user(nickname="Applicant")
    trip = _make_trip(client, owner)
    _apply(client, applicant, trip["id"])
    victim_notification = _notifications(client, owner)["items"][0]

    resp = client.delete(
        f"/api/v1/notifications/{victim_notification['id']}",
        headers=applicant["headers"],
    )
    assert resp.status_code == 404
    assert _unread(client, owner) == 1


def test_notifications_require_auth(client):
    assert client.get("/api/v1/notifications").status_code == 401
    assert client.get("/api/v1/notifications/unread-count").status_code == 401


# --------------------------------------------------------------------------
# Read / delete lifecycle
# --------------------------------------------------------------------------

def test_mark_read_and_read_all(client, register_user):
    owner = register_user(nickname="Owner")
    trip = _make_trip(client, owner)
    for i in range(3):
        applicant = register_user(nickname=f"Applicant{i}")
        _apply(client, applicant, trip["id"])

    page = _notifications(client, owner)
    assert page["unread"] == 3 and page["total"] == 3

    first_id = page["items"][0]["id"]
    resp = client.post(f"/api/v1/notifications/{first_id}/read", headers=owner["headers"])
    assert resp.status_code == 200
    assert resp.json()["read_at"] is not None
    assert _unread(client, owner) == 2

    # Idempotent: marking the same one twice must not decrement again.
    client.post(f"/api/v1/notifications/{first_id}/read", headers=owner["headers"])
    assert _unread(client, owner) == 2

    resp = client.post("/api/v1/notifications/read-all", headers=owner["headers"])
    assert resp.status_code == 200
    assert resp.json()["unread"] == 0
    assert _unread(client, owner) == 0


def test_delete_notification(client, register_user):
    owner = register_user(nickname="Owner")
    applicant = register_user(nickname="Applicant")
    trip = _make_trip(client, owner)
    _apply(client, applicant, trip["id"])

    notification_id = _notifications(client, owner)["items"][0]["id"]
    resp = client.delete(
        f"/api/v1/notifications/{notification_id}", headers=owner["headers"]
    )
    assert resp.status_code == 204
    assert _notifications(client, owner)["total"] == 0


def test_unread_only_filter_and_pagination(client, register_user):
    owner = register_user(nickname="Owner")
    trip = _make_trip(client, owner)
    for i in range(5):
        applicant = register_user(nickname=f"Pager{i}")
        _apply(client, applicant, trip["id"])

    # Read the two newest.
    items = _notifications(client, owner)["items"]
    for item in items[:2]:
        client.post(f"/api/v1/notifications/{item['id']}/read", headers=owner["headers"])

    page = _notifications(client, owner, unread_only=True)
    assert page["total"] == 3
    assert page["unread"] == 3
    assert all(i["read_at"] is None for i in page["items"])

    # Pagination is stable and reports the full total.
    first = _notifications(client, owner, page=1, page_size=2)
    second = _notifications(client, owner, page=2, page_size=2)
    assert first["total"] == 5 and second["total"] == 5
    assert len(first["items"]) == 2
    assert {i["id"] for i in first["items"]}.isdisjoint({i["id"] for i in second["items"]})
