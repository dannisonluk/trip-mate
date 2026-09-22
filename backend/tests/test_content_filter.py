"""Content-filter tests.

Two things are being defended here, and the second matters more than the first:

1. That the filter rejects what it should.
2. That it **allows** ordinary travel talk. A false positive is a real user who
   cannot publish a trip, which is a worse outcome than one more item in the
   report queue — so the "legitimate text" corpus below is the important test,
   not the blocked-phrase list.

A note on status codes: FastAPI returns 422 for a *schema* violation too, so an
assertion of `status_code == 422` alone would pass even if the filter never ran.
Every endpoint test therefore also asserts on the message.
"""
import asyncio
import uuid

import httpx
import pytest

from app.core import metrics
from app.core.config import settings
from app.services import content_filter
from app.services.content_filter import Verdict, check_local

REJECTION_MARKER = "社群規範"


def _token(headers: dict) -> str:
    return headers["Authorization"].split()[1]


def _recv_until(ws, wanted: str, limit: int = 25) -> dict:
    for _ in range(limit):
        data = ws.receive_json()
        if data.get("type") == wanted:
            return data
    raise AssertionError(f"Never received a {wanted!r} frame")


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


def _accepted_room(client, alice, bob) -> str:
    trip = _make_trip(client, alice)
    application = client.post(
        f"/api/v1/trips/{trip['id']}/apply", headers=bob["headers"], json={"message": "Me!"}
    ).json()
    client.patch(
        f"/api/v1/trips/applications/{application['id']}?decision=ACCEPTED",
        headers=alice["headers"],
    )
    rooms = client.get("/api/v1/chat/rooms", headers=alice["headers"]).json()
    return rooms[0]["id"]


# --------------------------------------------------------------------------
# The important one: legitimate travel text must not be blocked
# --------------------------------------------------------------------------

LEGITIMATE = [
    # "deposit" is ordinary accommodation/rental talk.
    "We can share a hotel room and split the deposit.",
    "The hotel asked for a deposit, is that normal for Japan?",
    "Renting a campervan needs a credit card deposit.",
    # "kill", "hurt", "shoot" in innocent senses.
    "The view from the summit was a killer view!",
    "We will kill time at the airport before the night flight.",
    "I am going to shoot photos of the lantern festival.",
    "This trek will hurt your knees, bring poles.",
    # Place names that look like denylist entries.
    "I want to visit Gunsan in Korea, then Busan.",
    "Planning a trip to Scunthorpe? Not really my thing.",
    # Talking *about* scams is not committing one.
    "How do we avoid tuk-tuk scams in Bangkok?",
    "I read about the fake taxi scam at the airport.",
    "My friend got scammed, so now I always book direct.",
    # Payment in a normal sense.
    "I will send you the money for the train ticket when we meet.",
    "Split the hostel fee at the end, that is easiest.",
    "Transfer the booking to my name if you can.",
    # Drugs in a benign context (the rule requires a buy/sell verb).
    "The drugstore is next to the station.",
    "Is there a pharmacy open late in Osaka?",
    # Family / health.
    "Travelling with my wife and our two kids.",
    "I have a nut allergy, so I bring my own snacks.",
    # Off-platform contact is a flag, never a block.
    "Add me on WhatsApp if you want to plan faster.",
    "Here is the itinerary: https://bit.ly/abc123",
    "WeChat is blocked where I am, use the app chat.",
]


@pytest.mark.parametrize("text", LEGITIMATE)
def test_ordinary_travel_text_is_never_blocked(text):
    assert check_local(text).action != "BLOCK", f"false positive on: {text!r}"


@pytest.mark.parametrize("text", LEGITIMATE)
def test_ordinary_travel_text_passes_enforce(text):
    verdict = asyncio.run(content_filter.enforce(text, field="chat"))
    assert verdict.action in ("ALLOW", "FLAG")


# --------------------------------------------------------------------------
# Local rules
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("escort service available, dm me", "sexual_solicitation"),
        ("full service massage in my room", "sexual_solicitation"),
        ("I will kill you if you do not pay", "violent_threat"),
        ("send the deposit via Western Union", "off_platform_payment"),
        ("use MoneyGram, it is faster", "off_platform_payment"),
        ("want to buy cocaine? cheap price", "illegal_goods"),
        ("I can sell you some mdma", "illegal_goods"),
    ],
)
def test_blocking_rules_fire(text, reason):
    verdict = check_local(text)
    assert verdict.action == "BLOCK"
    assert verdict.reason == reason


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("transfer me the money first and I'll book", "payment_solicitation"),
        ("send me the deposit before we meet", "payment_solicitation"),
        ("add me on telegram: @someone", "off_platform_contact"),
        ("book here https://tinyurl.com/xyz", "link_heavy"),
    ],
)
def test_flagging_rules_fire(text, reason):
    verdict = check_local(text)
    assert verdict.action == "FLAG"
    assert verdict.reason == reason


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_text_is_allowed(text):
    assert check_local(text).action == "ALLOW"


def test_a_block_short_circuits_before_a_flag():
    """When both would match, the block is what is returned."""
    verdict = check_local("escort service, add me on telegram")
    assert verdict.action == "BLOCK"
    assert verdict.reason == "sexual_solicitation"


def test_verdict_rejects_an_invalid_action():
    with pytest.raises(AssertionError):
        Verdict("MAYBE")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Cheap-evasion normalisation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "ｅｓｃｏｒｔ ｓｅｒｖｉｃｅ",          # full-width forms
        "escort\u200bservice",                  # zero-width space
        "escort\u200d service",                 # zero-width joiner
        "escort\tservice",                      # tab instead of space
        "escort  service",                      # doubled space
        "  escort service  ",                   # padding
        "ESCORT SERVICE",                       # case
    ],
)
def test_cheap_evasions_are_normalised_away(text):
    assert check_local(text).action == "BLOCK", text


def test_normalisation_does_not_merge_across_words():
    """A word-splitting trick must not become a false positive elsewhere."""
    # Collapsing all whitespace would turn this into "drugstore" style adjacency
    # problems; the rule needs a verb, so it stays allowed.
    assert check_local("the drug store is closed").action == "ALLOW"


# --------------------------------------------------------------------------
# enforce / screen
# --------------------------------------------------------------------------


def test_enforce_raises_422_with_a_message_that_names_the_field():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            content_filter.enforce(
                "send the deposit via Western Union", field="trip", label="行程說明"
            )
        )
    assert caught.value.status_code == 422
    assert REJECTION_MARKER in caught.value.detail
    assert "行程說明" in caught.value.detail


def test_the_rejection_never_names_the_matched_rule_or_term():
    """Echoing the match back would hand an attacker the rule set one try at a time."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        asyncio.run(content_filter.enforce("pay with MoneyGram please", field="chat"))
    detail = str(caught.value.detail)
    for leaked in ("MoneyGram", "moneygram", "off_platform_payment", "western union"):
        assert leaked not in detail, detail


def test_screen_returns_the_detail_instead_of_raising():
    """The WebSocket path needs the verdict and the message separately."""
    verdict, detail = asyncio.run(
        content_filter.screen("use Western Union", field="chat", label="訊息")
    )
    assert verdict.action == "BLOCK"
    assert detail is not None and REJECTION_MARKER in detail


def test_screen_returns_no_detail_for_allowed_content():
    verdict, detail = asyncio.run(
        content_filter.screen("see you at the station", field="chat")
    )
    assert verdict.action == "ALLOW"
    assert detail is None


def test_enforce_many_names_the_offending_field():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            content_filter.enforce_many(
                field="trip",
                title="A perfectly fine title",
                description="send the deposit via Western Union",
            )
        )
    assert "行程說明" in caught.value.detail


def test_enforce_many_skips_empty_fields():
    asyncio.run(
        content_filter.enforce_many(field="trip", title="Fine title", description=None)
    )


def test_disabling_the_filter_allows_everything(monkeypatch):
    monkeypatch.setattr(settings, "CONTENT_FILTER_ENABLED", False)
    try:
        verdict = asyncio.run(
            content_filter.enforce("send the deposit via Western Union", field="chat")
        )
        assert verdict.action == "ALLOW"
    finally:
        monkeypatch.undo()


# --------------------------------------------------------------------------
# External provider
# --------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.request = httpx.Request("POST", "http://provider.test/moderate")

    def json(self):
        return self._payload


class _FakeClient:
    """Minimal stand-in for `httpx.AsyncClient`."""

    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, json=None, headers=None):  # noqa: A002
        if self._exc is not None:
            raise self._exc
        return self._response


def _use_provider(monkeypatch, *, response=None, exc=None, url="http://provider.test/moderate"):
    monkeypatch.setattr(settings, "CONTENT_FILTER_PROVIDER", "webhook")
    monkeypatch.setattr(settings, "CONTENT_WEBHOOK_URL", url)

    class _StubHttpx:
        AsyncClient = staticmethod(lambda **kwargs: _FakeClient(response, exc))
        HTTPStatusError = httpx.HTTPStatusError

    monkeypatch.setattr(content_filter, "httpx", _StubHttpx)


def test_provider_block_rejects_content(monkeypatch):
    _use_provider(
        monkeypatch, response=_FakeResponse(200, {"action": "block", "reason": "hate"})
    )
    try:
        verdict = asyncio.run(content_filter.moderate("something the vendor dislikes", field="chat"))
        assert verdict.action == "BLOCK"
        assert verdict.reason == "hate"
    finally:
        monkeypatch.undo()


def test_provider_is_not_consulted_when_local_rules_already_block(monkeypatch):
    """No point spending a network round-trip on a decided verdict."""
    _use_provider(monkeypatch, exc=AssertionError("provider must not be called"))
    try:
        verdict = asyncio.run(content_filter.moderate("escort service", field="chat"))
        assert verdict.action == "BLOCK"
        assert verdict.reason == "sexual_solicitation"
    finally:
        monkeypatch.undo()


def test_provider_failure_fails_open(monkeypatch):
    """A moderation outage must not become a total write outage."""
    _use_provider(monkeypatch, exc=httpx.ConnectError("provider down"))
    try:
        verdict = asyncio.run(
            content_filter.enforce("a normal message about trains", field="chat")
        )
        assert verdict.action == "ALLOW"
    finally:
        monkeypatch.undo()


def test_provider_failure_fails_open_but_still_honours_local_blocks(monkeypatch):
    """Failing open applies to the *provider*, not to the local rules."""
    from fastapi import HTTPException

    _use_provider(monkeypatch, exc=httpx.ConnectError("provider down"))
    try:
        with pytest.raises(HTTPException):
            asyncio.run(
                content_filter.enforce("send the deposit via Western Union", field="chat")
            )
    finally:
        monkeypatch.undo()


def test_provider_can_be_configured_to_fail_closed(monkeypatch):
    from fastapi import HTTPException

    _use_provider(monkeypatch, exc=httpx.ConnectError("provider down"))
    monkeypatch.setattr(settings, "CONTENT_FILTER_FAIL_OPEN", False)
    try:
        with pytest.raises(HTTPException) as caught:
            asyncio.run(content_filter.enforce("a normal message", field="chat"))
        assert caught.value.status_code == 503
    finally:
        monkeypatch.undo()


def test_an_unrecognised_provider_action_is_treated_as_no_opinion(monkeypatch):
    _use_provider(monkeypatch, response=_FakeResponse(200, {"action": "maybe"}))
    try:
        verdict = asyncio.run(content_filter.moderate("a normal message", field="chat"))
        assert verdict.action == "ALLOW"
    finally:
        monkeypatch.undo()


def test_a_missing_webhook_url_degrades_to_local_rules(monkeypatch):
    _use_provider(monkeypatch, url="")
    try:
        verdict = asyncio.run(content_filter.moderate("a normal message", field="chat"))
        assert verdict.action == "ALLOW"
    finally:
        monkeypatch.undo()


def test_provider_http_error_is_handled(monkeypatch):
    _use_provider(monkeypatch, response=_FakeResponse(500, text="boom"))
    try:
        verdict = asyncio.run(content_filter.moderate("a normal message", field="chat"))
        assert verdict.action == "ALLOW"  # fail-open
    finally:
        monkeypatch.undo()


# --------------------------------------------------------------------------
# Wired endpoints
# --------------------------------------------------------------------------


def test_trip_create_rejects_blocked_title(client, register_user):
    user = register_user()
    resp = client.post(
        "/api/v1/trips",
        headers=user["headers"],
        json={
            "title": "escort service available",
            "description": "Looking for a companion for the trip.",
            "destination_country": "Japan",
            "budget_type": "MODERATE",
            "target_gender": "ANY",
        },
    )
    assert resp.status_code == 422
    assert REJECTION_MARKER in resp.json()["detail"]
    assert "標題" in resp.json()["detail"]


def test_trip_create_rejects_blocked_description(client, register_user):
    user = register_user()
    resp = client.post(
        "/api/v1/trips",
        headers=user["headers"],
        json={
            "title": "A perfectly ordinary trip title",
            "description": "Please send the deposit via Western Union to confirm.",
            "destination_country": "Japan",
            "budget_type": "MODERATE",
            "target_gender": "ANY",
        },
    )
    assert resp.status_code == 422
    assert "行程說明" in resp.json()["detail"]


def test_trip_create_accepts_ordinary_text(client, register_user):
    user = register_user()
    trip = _make_trip(
        client,
        user,
        title="Split the hotel deposit in Kyoto",
        description="We can split the hotel deposit and kill time at the market together.",
    )
    assert trip["title"].startswith("Split the hotel")


def test_trip_update_is_moderated(client, register_user):
    user = register_user()
    trip = _make_trip(client, user)
    resp = client.patch(
        f"/api/v1/trips/{trip['id']}",
        headers=user["headers"],
        json={"description": "now pay with MoneyGram please, very safe"},
    )
    assert resp.status_code == 422
    assert REJECTION_MARKER in resp.json()["detail"]


def test_trip_update_ignores_stored_values_it_is_not_changing(client, register_user):
    """An unrelated edit must not re-litigate text that is already stored."""
    user = register_user()
    trip = _make_trip(client, user)
    # Only the title is being changed; the description is not re-checked.
    resp = client.patch(
        f"/api/v1/trips/{trip['id']}",
        headers=user["headers"],
        json={"title": "A revised and perfectly fine title"},
    )
    assert resp.status_code == 200, resp.text


def test_profile_update_rejects_blocked_bio(client, register_user):
    user = register_user()
    resp = client.put(
        "/api/v1/profiles/me",
        headers=user["headers"],
        json={"bio": "escort service available, dm me for rates"},
    )
    assert resp.status_code == 422
    assert "個人簡介" in resp.json()["detail"]


def test_profile_update_accepts_ordinary_bio(client, register_user):
    user = register_user()
    resp = client.put(
        "/api/v1/profiles/me",
        headers=user["headers"],
        json={"bio": "I like long hikes, street food and splitting the hotel deposit."},
    )
    assert resp.status_code == 200, resp.text


def test_travel_history_summary_is_moderated(client, register_user):
    user = register_user()
    resp = client.post(
        "/api/v1/profiles/me/histories",
        headers=user["headers"],
        json={
            "country": "Japan",
            "budget_type": "MODERATE",
            "summary": "pay with Western Union to reserve the tour",
        },
    )
    assert resp.status_code == 422
    assert REJECTION_MARKER in resp.json()["detail"]


def test_review_comment_is_moderated(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    trip = _make_trip(client, alice)
    application = client.post(
        f"/api/v1/trips/{trip['id']}/apply", headers=bob["headers"], json={"message": "Hi"}
    ).json()
    client.patch(
        f"/api/v1/trips/applications/{application['id']}?decision=ACCEPTED",
        headers=alice["headers"],
    )

    resp = client.post(
        "/api/v1/reviews",
        headers=bob["headers"],
        json={
            "reviewee_id": alice["profile"]["id"],
            "trip_post_id": trip["id"],
            "rating": 1,
            "comment": "escort service available, avoid this person",
        },
    )
    assert resp.status_code == 422
    assert "評價內容" in resp.json()["detail"]


def test_chat_rest_rejects_blocked_content(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    room_id = _accepted_room(client, alice, bob)

    resp = client.post(
        f"/api/v1/chat/rooms/{room_id}/messages",
        headers=alice["headers"],
        json={"content": "send the deposit via Western Union"},
    )
    assert resp.status_code == 422
    assert REJECTION_MARKER in resp.json()["detail"]

    # ...and nothing was stored.
    history = client.get(
        f"/api/v1/chat/rooms/{room_id}/messages", headers=alice["headers"]
    ).json()
    assert not any("Western Union" in m["content"] for m in history["items"])


def test_chat_rest_accepts_ordinary_content(client, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    room_id = _accepted_room(client, alice, bob)

    resp = client.post(
        f"/api/v1/chat/rooms/{room_id}/messages",
        headers=alice["headers"],
        json={"content": "Shall we split the hotel deposit?"},
    )
    assert resp.status_code == 201, resp.text


def test_websocket_rejects_blocked_content_without_storing_it(client, register_user):
    """The socket path signals with an error frame, not an HTTP status."""
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    room_id = _accepted_room(client, alice, bob)

    with client.websocket_connect(
        f"/api/v1/ws/chat/{room_id}?token={_token(alice['headers'])}"
    ) as ws:
        ws.send_json({"type": "message", "content": "use MoneyGram, safer"})
        frame = _recv_until(ws, "error")
        assert frame["code"] == "content_rejected"
        assert REJECTION_MARKER in frame["detail"]

        # A clean message still goes through on the same connection. Waiting for
        # the echo is what makes the storage assertion below deterministic —
        # closing the socket immediately would race the server.
        ws.send_json({"type": "message", "content": "See you at Kyoto station!"})
        echoed = _recv_until(ws, "message")
        assert "Kyoto station" in echoed["content"]

    history = client.get(
        f"/api/v1/chat/rooms/{room_id}/messages", headers=bob["headers"]
    ).json()
    contents = [m["content"] for m in history["items"]]
    assert any("Kyoto station" in c for c in contents)
    assert not any("MoneyGram" in c for c in contents)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def test_a_blocked_write_increments_the_counter(client, register_user):
    user = register_user()
    client.post(
        "/api/v1/trips",
        headers=user["headers"],
        json={
            "title": "escort service available",
            "description": "Looking for a companion for the trip.",
            "destination_country": "Japan",
            "budget_type": "MODERATE",
            "target_gender": "ANY",
        },
    )
    body = metrics.render()[0].decode()
    assert 'tripmate_content_blocked_total{field="trip",reason="sexual_solicitation"}' in body


def test_a_flagged_write_increments_the_flag_counter(client, register_user):
    user = register_user()
    trip = _make_trip(client, user)
    resp = client.patch(
        f"/api/v1/trips/{trip['id']}",
        headers=user["headers"],
        json={"description": "Add me on WhatsApp to plan the route together."},
    )
    assert resp.status_code == 200, resp.text
    body = metrics.render()[0].decode()
    assert 'tripmate_content_flagged_total{field="trip",reason="off_platform_contact"}' in body


def test_the_metric_never_carries_the_text_or_the_author(client, register_user):
    """Labels are closed sets; a raw string would blow up cardinality."""
    user = register_user()
    marker = uuid.uuid4().hex
    client.post(
        "/api/v1/trips",
        headers=user["headers"],
        json={
            "title": f"escort service {marker}",
            "description": "Looking for a companion for the trip.",
            "destination_country": "Japan",
            "budget_type": "MODERATE",
            "target_gender": "ANY",
        },
    )
    body = metrics.render()[0].decode()
    assert marker not in body
    assert str(user["profile"]["id"]) not in body
