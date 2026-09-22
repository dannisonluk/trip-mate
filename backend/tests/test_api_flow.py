"""End-to-end API flow tests covering the Trip Mate spec + Security & Privacy Spec."""
import pytest
from starlette.websockets import WebSocketDisconnect


def _token(headers: dict) -> str:
    return headers["Authorization"].split()[1]


def _recv_until(ws, wanted: str, limit: int = 25) -> dict:
    """Read frames until one of the wanted type arrives (presence frames come first)."""
    for _ in range(limit):
        data = ws.receive_json()
        if data.get("type") == wanted:
            return data
    raise AssertionError(f"Never received a {wanted!r} frame")


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


# --------------------------------------------------------------------------
# Meta / headers
# --------------------------------------------------------------------------

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_security_headers_present(client):
    resp = client.get("/health")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in resp.headers


# --------------------------------------------------------------------------
# Auth (§1.1, §1.2)
# --------------------------------------------------------------------------

def test_register_requires_consent(client):
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone_number": "+85291110001",
            "password": "Passw0rd123",
            "nickname": "NoConsent",
            "consent_privacy": False,
            "consent_terms": True,
        },
    )
    assert resp.status_code == 422


def test_weak_password_rejected(client):
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone_number": "+85291110002",
            "password": "alllowercase",
            "nickname": "Weak",
            "consent_privacy": True,
            "consent_terms": True,
        },
    )
    assert resp.status_code == 422


def test_invalid_phone_rejected(client):
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone_number": "+15551234567",
            "password": "Passw0rd123",
            "nickname": "Foreigner",
            "consent_privacy": True,
            "consent_terms": True,
        },
    )
    assert resp.status_code == 422


def test_login_is_generic_on_failure(client, register_user):
    user = register_user()
    bad = client.post(
        "/api/v1/auth/login", json={"phone_number": user["phone"], "password": "WrongPass123"}
    )
    unknown = client.post(
        "/api/v1/auth/login",
        json={"phone_number": "+85298887777", "password": "WrongPass123"},
    )
    assert bad.status_code == unknown.status_code == 401
    # Identical message → no account enumeration.
    assert bad.json()["detail"] == unknown.json()["detail"]


def test_login_burns_hash_time_for_unknown_account(client, register_user, monkeypatch):
    """An unknown phone number must still cost one Argon2 verification.

    Identical error *messages* are not enough: if the "no such user" branch
    short-circuits past `verify_password`, it returns measurably faster and the
    latency itself becomes an enumeration oracle. We assert on the call rather
    than on wall-clock time so the test is deterministic under CI load.

    Patch the primitives in `core.security` (not the router's imported names):
    the router calls the async wrappers, which look the sync function up in the
    security module's globals at call time.
    """
    from app.core import security

    calls = {"dummy": 0, "real": 0}
    real_verify = security.verify_password

    def counting_dummy(password: str) -> None:
        calls["dummy"] += 1

    def counting_real(password: str, hashed: str) -> bool:
        calls["real"] += 1
        return real_verify(password, hashed)

    monkeypatch.setattr(security, "verify_password_dummy", counting_dummy)
    monkeypatch.setattr(security, "verify_password", counting_real)

    # 1. Unknown account → the dummy verification must run exactly once.
    unknown = client.post(
        "/api/v1/auth/login",
        json={"phone_number": "+85298880001", "password": "WrongPass123"},
    )
    assert unknown.status_code == 401
    assert calls["dummy"] == 1, "unknown-account branch skipped the timing defence"
    assert calls["real"] == 0

    # 2. Known account, wrong password → the real verification runs instead.
    user = register_user()
    calls.update(dummy=0, real=0)
    bad = client.post(
        "/api/v1/auth/login", json={"phone_number": user["phone"], "password": "WrongPass123"}
    )
    assert bad.status_code == 401
    assert calls["real"] == 1
    assert calls["dummy"] == 0


def test_otp_flow_sets_is_verified(client, register_user):
    user = register_user()
    me = client.get("/api/v1/auth/me", headers=user["headers"]).json()
    assert me["is_verified"] is False

    req = client.post("/api/v1/auth/otp/request", json={"phone_number": user["phone"]})
    assert req.status_code == 200
    code = req.json()["dev_code"]
    assert code and len(code) == 6

    verified = client.post(
        "/api/v1/auth/otp/verify", json={"phone_number": user["phone"], "code": code}
    )
    assert verified.status_code == 204
    assert client.get("/api/v1/auth/me", headers=user["headers"]).json()["is_verified"] is True


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------

def test_profile_update_and_mbti_validation(client, register_user):
    user = register_user(nickname="Alice")
    ok = client.put(
        "/api/v1/profiles/me",
        headers=user["headers"],
        json={
            "bio": "Love hiking",
            "mbti": "enfp",
            "travel_style_tags": ["HIKING", "FOOD"],
            "languages": ["CANTONESE", "ENGLISH"],
            "gender": "FEMALE",
        },
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["mbti"] == "ENFP"  # normalised to upper case
    assert body["gender"] == "FEMALE"
    assert body["stats"]["trips_count"] == 0

    bad = client.put("/api/v1/profiles/me", headers=user["headers"], json={"mbti": "XXXX"})
    assert bad.status_code == 422


def test_histories_privacy(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")

    public_entry = client.post(
        "/api/v1/profiles/me/histories",
        headers=alice["headers"],
        json={"country": "Japan", "city": "Tokyo", "is_public": True, "budget_type": "MODERATE"},
    )
    private_entry = client.post(
        "/api/v1/profiles/me/histories",
        headers=alice["headers"],
        json={"country": "France", "city": "Paris", "is_public": False},
    )
    assert public_entry.status_code == 201
    assert private_entry.status_code == 201

    alice_view = client.get(
        f"/api/v1/profiles/{alice['profile']['id']}/histories", headers=alice["headers"]
    ).json()
    assert len(alice_view) == 2  # owner sees both

    bob_view = client.get(
        f"/api/v1/profiles/{alice['profile']['id']}/histories", headers=bob["headers"]
    ).json()
    assert len(bob_view) == 1  # others only see public entries
    assert bob_view[0]["city"] == "Tokyo"


def test_profile_lookup_by_user_id_and_profile_id(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")

    by_profile_id = client.get(
        f"/api/v1/profiles/{alice['profile']['id']}", headers=bob["headers"]
    )
    assert by_profile_id.status_code == 200

    by_user_id = client.get(
        f"/api/v1/profiles/{alice['profile']['user_id']}", headers=bob["headers"]
    )
    assert by_user_id.status_code == 200
    assert by_user_id.json()["id"] == alice["profile"]["id"]


# --------------------------------------------------------------------------
# Trips
# --------------------------------------------------------------------------

def test_trip_create_and_filters(client, register_user):
    alice = register_user(nickname="Alice")
    trip = _make_trip(client, alice, destination_country="Japan", destination_city="Tokyo")

    listing = client.get("/api/v1/trips", headers=alice["headers"])
    assert listing.status_code == 200
    assert any(t["id"] == trip["id"] for t in listing.json()["items"])
    assert listing.json()["limit"] == 20

    # country filter
    filtered = client.get("/api/v1/trips?country=Japan", headers=alice["headers"]).json()
    assert all(t["destination_country"].lower() == "japan" for t in filtered["items"])

    # budget filter
    by_budget = client.get("/api/v1/trips?budget_type=LUXURY", headers=alice["headers"]).json()
    assert by_budget["total"] == 0

    # tag filter
    by_tag = client.get("/api/v1/trips?tags=PHOTOGRAPHY", headers=alice["headers"]).json()
    assert any(t["id"] == trip["id"] for t in by_tag["items"])

    # no match
    by_bad_tag = client.get("/api/v1/trips?tags=NOPE", headers=alice["headers"]).json()
    assert by_bad_tag["total"] == 0


def test_apply_duplicate_and_block_flow(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    trip = _make_trip(client, alice)

    apply_resp = client.post(
        f"/api/v1/trips/{trip['id']}/apply", headers=bob["headers"], json={"message": "Me!"}
    )
    assert apply_resp.status_code == 201
    assert apply_resp.json()["status"] == "PENDING"

    dup = client.post(
        f"/api/v1/trips/{trip['id']}/apply", headers=bob["headers"], json={"message": "again"}
    )
    assert dup.status_code == 409

    own = client.post(
        f"/api/v1/trips/{trip['id']}/apply", headers=alice["headers"], json={"message": "me"}
    )
    assert own.status_code == 400

    # Alice blocks Bob → Bob can no longer see or reach Alice.
    assert (
        client.post(
            f"/api/v1/profiles/{alice['profile']['id']}/block", headers=bob["headers"]
        ).status_code
        == 201
    )
    assert (
        client.get(
            f"/api/v1/profiles/{alice['profile']['id']}", headers=bob["headers"]
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/trips/{trip['id']}", headers=bob["headers"]).status_code == 404
    )


def test_trip_detail_readable_and_hides_applications(client, register_user):
    """GET /trips/{id} must return 200 — and only the organiser sees applicants.

    Regression: `TripPost.applications` is declared `lazy="raise"`, so validating
    the ORM object straight into `TripPostDetail` raised InvalidRequestError and
    the endpoint answered 500 for *every* viewer. Only the blocked-viewer 404
    path was covered before, which returns before validation — so the happy path
    had never actually been exercised, and the bug reached the frontend.
    """
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    trip = _make_trip(client, alice)

    # The organiser can read their own trip.
    owner_view = client.get(f"/api/v1/trips/{trip['id']}", headers=alice["headers"])
    assert owner_view.status_code == 200
    assert owner_view.json()["id"] == trip["id"]

    # A non-owner can read it too, but the applicant list is not public.
    other_view = client.get(f"/api/v1/trips/{trip['id']}", headers=bob["headers"])
    assert other_view.status_code == 200
    assert other_view.json()["applications"] == []

    # Once someone applies, the organiser sees it and the applicant does not.
    applied = client.post(
        f"/api/v1/trips/{trip['id']}/apply", headers=bob["headers"], json={"message": "Hi"}
    )
    assert applied.status_code == 201

    owner_after = client.get(f"/api/v1/trips/{trip['id']}", headers=alice["headers"])
    assert len(owner_after.json()["applications"]) == 1
    assert owner_after.json()["applications"][0]["applicant_id"] == bob["profile"]["id"]

    other_after = client.get(f"/api/v1/trips/{trip['id']}", headers=bob["headers"])
    assert other_after.json()["applications"] == []


def test_accept_application_creates_chat_room(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    trip = _make_trip(client, alice)

    application = client.post(
        f"/api/v1/trips/{trip['id']}/apply", headers=bob["headers"], json={"message": "Hi"}
    ).json()

    # Only the creator may decide.
    forbidden = client.patch(
        f"/api/v1/trips/applications/{application['id']}?decision=ACCEPTED",
        headers=bob["headers"],
    )
    assert forbidden.status_code == 404

    accepted = client.patch(
        f"/api/v1/trips/applications/{application['id']}?decision=ACCEPTED",
        headers=alice["headers"],
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "ACCEPTED"

    rooms = client.get("/api/v1/chat/rooms", headers=alice["headers"]).json()
    assert len(rooms) == 1
    assert rooms[0]["room_type"] == "DIRECT"
    assert len(rooms[0]["members"]) == 2


def test_recommendations_rank_by_history(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")

    _make_trip(client, alice, destination_country="Japan", destination_city="Tokyo",
               tags=["PHOTOGRAPHY"])

    # Bob's history is Tokyo + he shares the photography tag.
    client.post(
        "/api/v1/profiles/me/histories",
        headers=bob["headers"],
        json={"country": "Japan", "city": "Tokyo", "is_public": True},
    )
    client.put(
        "/api/v1/profiles/me",
        headers=bob["headers"],
        json={"travel_style_tags": ["PHOTOGRAPHY"], "languages": ["CANTONESE"]},
    )

    recs = client.get("/api/v1/trips/recommendations", headers=bob["headers"]).json()
    assert len(recs) >= 1
    assert recs[0]["score"] > 0
    assert any("Tokyo" in reason for reason in recs[0]["reasons"])


# --------------------------------------------------------------------------
# Reviews
# --------------------------------------------------------------------------

def test_review_requires_shared_trip(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    carla = register_user(nickname="Carla")
    trip = _make_trip(client, alice)

    # Bob has NOT travelled with Alice yet.
    early = client.post(
        "/api/v1/reviews",
        headers=bob["headers"],
        json={"reviewee_id": alice["profile"]["id"], "rating": 5},
    )
    assert early.status_code == 403

    application = client.post(
        f"/api/v1/trips/{trip['id']}/apply", headers=bob["headers"], json={"message": "Hi"}
    ).json()
    client.patch(
        f"/api/v1/trips/applications/{application['id']}?decision=ACCEPTED",
        headers=alice["headers"],
    )

    ok = client.post(
        "/api/v1/reviews",
        headers=bob["headers"],
        json={
            "reviewee_id": alice["profile"]["id"],
            "trip_post_id": trip["id"],
            "rating": 5,
            "tags": ["PUNCTUAL"],
            "comment": "Great travel buddy!",
        },
    )
    assert ok.status_code == 201, ok.text

    # Duplicate review for the same trip is rejected.
    dup = client.post(
        "/api/v1/reviews",
        headers=bob["headers"],
        json={"reviewee_id": alice["profile"]["id"], "trip_post_id": trip["id"], "rating": 4},
    )
    assert dup.status_code == 409

    # Carla never travelled with Alice.
    blocked = client.post(
        "/api/v1/reviews",
        headers=carla["headers"],
        json={"reviewee_id": alice["profile"]["id"], "rating": 1},
    )
    assert blocked.status_code == 403

    listed = client.get(
        f"/api/v1/profiles/{alice['profile']['id']}/reviews", headers=carla["headers"]
    ).json()
    assert len(listed) == 1 and listed[0]["rating"] == 5

    summary = client.get(
        f"/api/v1/profiles/{alice['profile']['id']}/reviews/summary", headers=carla["headers"]
    ).json()
    assert summary["count"] == 1
    assert summary["average_rating"] == 5.0


# --------------------------------------------------------------------------
# Privacy / account deletion
# --------------------------------------------------------------------------

def test_account_deletion_anonymize(client, register_user):
    user = register_user(nickname="ToDelete")
    resp = client.request(
        "DELETE",
        "/api/v1/users/me",
        headers=user["headers"],
        json={"password": user["password"], "mode": "anonymize"},
    )
    assert resp.status_code == 204

    # Token no longer usable (account deactivated).
    assert client.get("/api/v1/auth/me", headers=user["headers"]).status_code == 401


def test_upload_rejects_non_image(client, register_user):
    user = register_user(nickname="Uploader")
    resp = client.post(
        "/api/v1/uploads/image",
        headers=user["headers"],
        files={"file": ("evil.svg", b"<svg onload=alert(1)></svg>", "image/svg+xml")},
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# WebSocket chat (§3.1 handshake auth, §3.2 rate limiting)
# --------------------------------------------------------------------------

def _accepted_room(client, alice, bob) -> str:
    """Create a trip, have Bob apply, Alice accept → returns the auto-created room id."""
    trip = _make_trip(client, alice, title="Osaka autumn trip")
    application = client.post(
        f"/api/v1/trips/{trip['id']}/apply", headers=bob["headers"], json={"message": "Me!"}
    ).json()
    decided = client.patch(
        f"/api/v1/trips/applications/{application['id']}?decision=ACCEPTED",
        headers=alice["headers"],
    )
    assert decided.status_code == 200
    rooms = client.get("/api/v1/chat/rooms", headers=alice["headers"]).json()
    assert len(rooms) == 1
    return rooms[0]["id"]


def test_websocket_rejects_invalid_token(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    room_id = _accepted_room(client, alice, bob)

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/api/v1/ws/chat/{room_id}?token=not-a-real-token"):
            pass
    assert exc.value.code == 1008


def test_websocket_rejects_non_member(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    carla = register_user(nickname="Carla")
    room_id = _accepted_room(client, alice, bob)

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/api/v1/ws/chat/{room_id}?token={_token(carla['headers'])}"
        ):
            pass
    assert exc.value.code == 1008


def test_websocket_broadcast_and_history(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    room_id = _accepted_room(client, alice, bob)

    with client.websocket_connect(
        f"/api/v1/ws/chat/{room_id}?token={_token(alice['headers'])}"
    ) as alice_ws:
        with client.websocket_connect(
            f"/api/v1/ws/chat/{room_id}?token={_token(bob['headers'])}"
        ) as bob_ws:
            alice_ws.send_json({"type": "ping"})
            assert _recv_until(alice_ws, "pong")["type"] == "pong"

            alice_ws.send_json({"type": "message", "content": "Hello Bob!"})
            delivered = _recv_until(bob_ws, "message")
            assert delivered["content"] == "Hello Bob!"
            assert delivered["sender_id"] == alice["profile"]["id"]

    history = client.get(f"/api/v1/chat/rooms/{room_id}/messages", headers=bob["headers"]).json()
    assert any(m["content"] == "Hello Bob!" for m in history["items"])


def test_websocket_rate_limit_blocks_burst(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    room_id = _accepted_room(client, alice, bob)

    with client.websocket_connect(
        f"/api/v1/ws/chat/{room_id}?token={_token(alice['headers'])}"
    ) as ws:
        # Capacity is 2 tokens → the 3rd immediate send must be throttled.
        for i in range(5):
            ws.send_json({"type": "message", "content": f"burst {i}"})

        saw_rate_limit = False
        for _ in range(15):
            frame = ws.receive_json()
            if frame.get("type") == "error" and frame.get("code") == "rate_limited":
                saw_rate_limit = True
                break
        assert saw_rate_limit, "expected a rate_limited error frame"
