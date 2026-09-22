"""SMS provider selection and failure handling.

The important property is that delivery problems never surface as a request
failure — an SMS outage must not look like a signup bug, and must not reveal
whether a phone number is registered.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.services import sms


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """Stand-in for httpx.AsyncClient that records the outgoing request."""

    captured: dict = {}
    response = _FakeResponse(200)

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeClient.captured = {"url": url, "json": json, "headers": headers}
        return _FakeClient.response


@pytest.fixture()
def webhook(monkeypatch):
    monkeypatch.setattr(settings, "SMS_PROVIDER", "webhook")
    monkeypatch.setattr(settings, "SMS_WEBHOOK_URL", "https://sms.example.test/send")
    monkeypatch.setattr(settings, "SMS_WEBHOOK_TOKEN", "secret-token")
    monkeypatch.setattr(settings, "SMS_SENDER_ID", "TripMate")
    monkeypatch.setattr(sms.httpx, "AsyncClient", _FakeClient)
    _FakeClient.captured = {}
    _FakeClient.response = _FakeResponse(200)
    return _FakeClient


def test_console_provider_reports_success(monkeypatch):
    monkeypatch.setattr(settings, "SMS_PROVIDER", "console")
    assert asyncio.run(sms.send_verification_code("+85291234567", "123456")) is True


def test_webhook_posts_phone_code_and_token(webhook):
    ok = asyncio.run(sms.send_verification_code("+85291234567", "654321"))
    assert ok is True

    sent = webhook.captured
    assert sent["url"] == "https://sms.example.test/send"
    assert sent["json"]["phone_number"] == "+85291234567"
    assert sent["json"]["code"] == "654321"
    assert sent["json"]["sender_id"] == "TripMate"
    assert sent["headers"]["Authorization"] == "Bearer secret-token"


def test_webhook_without_url_fails_softly(monkeypatch):
    monkeypatch.setattr(settings, "SMS_PROVIDER", "webhook")
    monkeypatch.setattr(settings, "SMS_WEBHOOK_URL", "")
    # Must return False, not raise.
    assert asyncio.run(sms.send_verification_code("+85291234567", "111111")) is False


def test_gateway_error_is_swallowed(webhook):
    webhook.response = _FakeResponse(500, "boom")
    assert asyncio.run(sms.send_verification_code("+85291234567", "222222")) is False


def test_transport_error_is_swallowed(monkeypatch):
    monkeypatch.setattr(settings, "SMS_PROVIDER", "webhook")
    monkeypatch.setattr(settings, "SMS_WEBHOOK_URL", "https://sms.example.test/send")

    class _Exploding(_FakeClient):
        async def post(self, *a, **kw):
            raise ConnectionError("network down")

    monkeypatch.setattr(sms.httpx, "AsyncClient", _Exploding)
    assert asyncio.run(sms.send_verification_code("+85291234567", "333333")) is False


def test_readiness_check_flags_console_provider(monkeypatch):
    monkeypatch.setattr(settings, "SMS_PROVIDER", "console")
    ready, reason = sms.provider_is_production_ready()
    assert ready is False
    assert "console" in reason


def test_readiness_check_flags_missing_webhook_url(monkeypatch):
    monkeypatch.setattr(settings, "SMS_PROVIDER", "webhook")
    monkeypatch.setattr(settings, "SMS_WEBHOOK_URL", "")
    ready, reason = sms.provider_is_production_ready()
    assert ready is False
    assert "SMS_WEBHOOK_URL" in reason


def test_readiness_check_passes_for_configured_webhook(webhook):
    ready, reason = sms.provider_is_production_ready()
    assert ready is True
    assert reason == ""


def test_otp_endpoint_reports_delivery(client, register_user):
    """The dev console provider counts as a successful delivery."""
    user = register_user()
    resp = client.post("/api/v1/auth/otp/request", json={"phone_number": user["phone"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sent"] is True
    # Development + OTP_DEV_ECHO=true → the code is echoed for testability.
    assert body["dev_code"]


def test_dev_code_absent_when_echo_disabled(client, register_user, monkeypatch):
    monkeypatch.setattr(settings, "OTP_DEV_ECHO", False)
    user = register_user()
    resp = client.post("/api/v1/auth/otp/request", json={"phone_number": user["phone"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["dev_code"] is None


def test_dev_code_never_returned_in_production(client, register_user, monkeypatch):
    """OTP_DEV_ECHO must be ignored when ENV=production.

    A stale `.env` copied from development is the realistic failure mode here:
    the setting is still `true`, but returning a live OTP in an API response
    would hand an attacker the second factor outright.
    """
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "OTP_DEV_ECHO", True)  # deliberately still on
    user = register_user()
    resp = client.post("/api/v1/auth/otp/request", json={"phone_number": user["phone"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sent"] is True
    assert body["dev_code"] is None, "OTP code leaked in production response"
