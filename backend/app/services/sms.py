"""SMS delivery for OTP codes.

Two providers behind one interface, selected by `SMS_PROVIDER`:

* `console` — logs the code. Development only: it is useful because the flow is
  fully exercisable without an account, but nobody actually receives a message.
* `webhook` — POSTs `{phone_number, code, sender_id}` to `SMS_WEBHOOK_URL` with a
  bearer token. This is the integration point for a real gateway: point it at a
  thin adapter that speaks your provider's protocol (Twilio, Nexmo, an internal
  service) rather than teaching this module every vendor's API.

Delivery failure is deliberately **not** fatal to the request. The code is already
stored and the user can retry; raising here would leak whether a phone number is
registered and would make an SMS outage look like a signup bug. Failures are
logged loudly instead.
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("tripmate.sms")


class SmsDeliveryError(RuntimeError):
    """Raised by a provider when the gateway rejects or cannot reach the send."""


async def _send_console(phone_number: str, code: str) -> None:
    # Never log the code in production — there it must travel only over SMS.
    if settings.is_production:
        logger.info("SMS(console): would send a verification code to %s", phone_number)
        return
    logger.warning(
        "SMS(console) — no real gateway configured. Verification code for %s is %s",
        phone_number,
        code,
    )


async def _send_webhook(phone_number: str, code: str) -> None:
    if not settings.SMS_WEBHOOK_URL:
        raise SmsDeliveryError("SMS_WEBHOOK_URL is not configured")

    headers = {"Content-Type": "application/json"}
    if settings.SMS_WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {settings.SMS_WEBHOOK_TOKEN}"

    payload = {
        "phone_number": phone_number,
        "code": code,
        "sender_id": settings.SMS_SENDER_ID,
        "ttl_seconds": settings.OTP_TTL_SECONDS,
    }

    async with httpx.AsyncClient(timeout=settings.SMS_TIMEOUT_SECONDS) as client:
        resp = await client.post(settings.SMS_WEBHOOK_URL, json=payload, headers=headers)
    if resp.status_code >= 400:
        raise SmsDeliveryError(
            f"SMS gateway returned {resp.status_code}: {resp.text[:200]}"
        )


_PROVIDERS = {
    "console": _send_console,
    "webhook": _send_webhook,
}


async def send_verification_code(phone_number: str, code: str) -> bool:
    """Deliver `code` to `phone_number`. Returns True when the gateway accepted it.

    Never raises: a delivery problem must not turn a successful OTP issuance into
    a 500, nor reveal whether the number is registered.
    """
    provider = _PROVIDERS.get(settings.SMS_PROVIDER)
    if provider is None:  # pragma: no cover — guarded by the Literal type
        logger.error("Unknown SMS_PROVIDER %r — code not delivered.", settings.SMS_PROVIDER)
        return False

    try:
        await provider(phone_number, code)
        return True
    except Exception as exc:  # noqa: BLE001 — any transport/provider failure
        logger.error(
            "SMS delivery failed via %r provider for %s: %s",
            settings.SMS_PROVIDER,
            phone_number,
            exc,
        )
        return False


def provider_is_production_ready() -> tuple[bool, str]:
    """Startup sanity check: will real users actually receive a code?"""
    if settings.SMS_PROVIDER == "console":
        return (
            False,
            "SMS_PROVIDER=console only logs codes — no SMS will be delivered. "
            "Set SMS_PROVIDER=webhook and SMS_WEBHOOK_URL before going live.",
        )
    if not settings.SMS_WEBHOOK_URL:
        return False, "SMS_PROVIDER=webhook requires SMS_WEBHOOK_URL to be set."
    return True, ""
