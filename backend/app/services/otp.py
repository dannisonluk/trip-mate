"""SMS OTP issuance / verification for phone-number signup (§3.2 rate-limited).

Storage is delegated to `services.kv` (Redis when reachable, in-process TTL dict
otherwise). Codes are never logged and never persisted in the database.
"""
import hmac
import random

from app.core.config import settings
from app.services import kv

_OTP_LENGTH = 6


def _key(phone: str) -> str:
    return f"tripmate:otp:{phone}"


def generate_code() -> str:
    return f"{random.SystemRandom().randint(0, 10**_OTP_LENGTH - 1):0{_OTP_LENGTH}d}"


async def issue_otp(phone_number: str) -> str:
    """Create and store a one-time code, returning it for delivery via SMS."""
    code = generate_code()
    await kv.set_value(_key(phone_number), code, settings.OTP_TTL_SECONDS)
    return code


async def verify_otp(phone_number: str, code: str) -> bool:
    """Constant-time comparison; the code is consumed on success."""
    stored = await kv.get_value(_key(phone_number))
    if not stored or not hmac.compare_digest(stored, code):
        return False
    await kv.delete_value(_key(phone_number))
    return True
