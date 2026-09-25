"""Notification system: fan-out, block-awareness, and access control.

The security-relevant properties here are:

* A block must suppress notifications in both directions. If it did not, the
  notification bell becomes a harassment channel that bypasses the block.
* Notifications are never readable by anyone but the recipient — enforced in the
  WHERE clause so a foreign id yields 404, not 403 (no existence leak).
* No self-notifications.
* **The server never chooses a language** (B6): a row carries a code and params,
  and the client composes the sentence. A server-rendered title would freeze
  whichever locale the writer happened to be using.
"""
from __future__ import annotations

import re

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
    # A language-neutral code plus params, not a rendered sentence: the server
    # must not decide what language this row reads in.
    assert item["code"] == "trip.application_received"
    assert item["params"]["actor"] == "Applicant"
    assert item["params"]["trip"] == trip["title"]
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
    assert page["items"][0]["code"] == "trip.application_accepted"
    assert page["items"][0]["params"]["actor"] == "Owner"


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


# --------------------------------------------------------------------------
# Localisation (B6) — the server must not choose a language
# --------------------------------------------------------------------------

_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def test_no_notification_field_carries_hardcoded_chinese(client, register_user):
    """Every server-authored field is language-neutral; only `body` holds prose.

    This is the regression guard for B6. The old code assembled a Chinese
    sentence into `title`, which meant an English-locale user read Traditional
    Chinese out of a card the server had rendered for a zh-HK user months
    earlier. The fix only holds if the *sentence* never comes back, so this
    asserts on the shape of the payload rather than on one endpoint.

    `body` is exempt by design: it is a preview of what another person typed, so
    it is Chinese because the sender wrote Chinese, and translating it would be
    falsifying their message.
    """
    owner = register_user(nickname="Owner")
    applicant = register_user(nickname="Applicant")
    trip = _make_trip(client, owner)
    application = _apply(client, applicant, trip["id"])
    _decide(client, owner, application["id"], "ACCEPTED")

    # Cover all three notification-producing paths so a single missed call site
    # cannot hide behind the others.
    client.post(
        "/api/v1/reviews",
        headers=applicant["headers"],
        json={"reviewee_id": owner["profile"]["id"], "rating": 5, "comment": "Great"},
    )
    rooms = client.get("/api/v1/chat/rooms", headers=applicant["headers"]).json()
    with client.websocket_connect(
        f"/api/v1/ws/chat/{rooms[0]['id']}?token={_token(applicant['headers'])}"
    ) as ws:
        _recv_until(ws, "presence")
        ws.send_json({"type": "message", "content": "Hello"})
        _recv_until(ws, "message")

    for viewer in (owner, applicant):
        for item in _notifications(client, viewer)["items"]:
            assert item["code"], f"no code on a {item['type']} notification"
            assert not _HAN.search(item["code"]), item["code"]
            for key, value in (item.get("params") or {}).items():
                # Params are ids, names and scalars — never a phrase. A name may
                # be Chinese (that is the user's own nickname); punctuation and
                # grammar must not be.
                assert "：" not in str(value), (key, value)
                assert "」" not in str(value), (key, value)
            assert "title" not in item, (
                "a rendered title came back — the client must compose the sentence"
            )


def test_room_scoped_message_notification_uses_the_room_code(client, register_user):
    """A TRIP room names itself, so its notification gets the room-aware code.

    Two codes rather than one with an optional label: the presence of the room
    name changes the sentence's shape, not just a substituted word.
    """
    owner = register_user(nickname="Owner")
    applicant = register_user(nickname="Applicant")
    trip = _make_trip(client, owner)
    application = _apply(client, applicant, trip["id"])
    _decide(client, owner, application["id"], "ACCEPTED")

    # The organiser opens the group room, which is what gives it a title.
    group = client.post(
        "/api/v1/chat/rooms",
        headers=owner["headers"],
        json={"room_type": "TRIP", "trip_post_id": trip["id"]},
    )
    assert group.status_code in (200, 201), group.text
    room_id = group.json()["id"]

    client.post("/api/v1/notifications/read-all", headers=applicant["headers"])
    with client.websocket_connect(
        f"/api/v1/ws/chat/{room_id}?token={_token(owner['headers'])}"
    ) as ws:
        _recv_until(ws, "presence")
        ws.send_json({"type": "message", "content": "Leaving at nine"})
        _recv_until(ws, "message")

    item = _notifications(client, applicant)["items"][0]
    assert item["code"] == "chat.new_message_in_room"
    assert item["params"]["room"] == trip["title"]
    assert item["params"]["actor"] == "Owner"


def test_direct_room_message_notification_uses_the_plain_code(client, register_user):
    """A DIRECT room has no label, so the sentence must not invent one.

    Naming it after the sender would read "Alice in Alice".
    """
    owner = register_user(nickname="Owner")
    applicant = register_user(nickname="Applicant")
    trip = _make_trip(client, owner)
    application = _apply(client, applicant, trip["id"])
    _decide(client, owner, application["id"], "ACCEPTED")
    client.post("/api/v1/notifications/read-all", headers=owner["headers"])

    rooms = client.get("/api/v1/chat/rooms", headers=applicant["headers"]).json()
    direct = next(r for r in rooms if r["room_type"] == "DIRECT")

    with client.websocket_connect(
        f"/api/v1/ws/chat/{direct['id']}?token={_token(applicant['headers'])}"
    ) as ws:
        _recv_until(ws, "presence")
        ws.send_json({"type": "message", "content": "hi"})
        _recv_until(ws, "message")

    item = _notifications(client, owner)["items"][0]
    assert item["code"] == "chat.new_message"
    assert "room" not in (item["params"] or {}), item["params"]
