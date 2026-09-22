"""Password hashing + JWT (dual-token) primitives.

Implements Security & Privacy Spec §1:
  * Argon2id password hashing
  * Dual-token: short-lived Access Token + 7-day Refresh Token
  * RS256 in production, HS256 fallback for local dev
  * Payload restricted to user_id / profile_id / role / exp / iat / type
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from anyio import to_thread
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from jose import JWTError, jwt

from app.core.config import settings

# --- Password hashing (Argon2id) -------------------------------------------
# time_cost=3, memory_cost=64 MiB, parallelism=4 → OWASP-recommended profile.
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

MIN_PASSWORD_LENGTH = 8


class PasswordPolicyError(ValueError):
    """Raised when a password fails the complexity policy."""


def validate_password_policy(password: str) -> None:
    """§1.2 — min 8 chars, must contain upper, lower and a digit."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if not any(c.islower() for c in password):
        raise PasswordPolicyError("Password must contain a lowercase letter.")
    if not any(c.isupper() for c in password):
        raise PasswordPolicyError("Password must contain an uppercase letter.")
    if not any(c.isdigit() for c in password):
        raise PasswordPolicyError("Password must contain a digit.")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _ph.check_needs_rehash(hashed)
    except InvalidHashError:
        return True


# A real Argon2id hash (same cost parameters as `_ph`) of a random throwaway
# string. Verifying against it costs the same ~33 ms as verifying a genuine
# account, which is the whole point: it lets the login path burn the same CPU
# time for "no such account" as for "wrong password", so response latency
# cannot be used to enumerate registered phone numbers (§1.2).
_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$1XPq1Sshmgm3g8jZ1b2l3Q$"
    "9x58YEnTyJ/CCYDbE713W39lj9tbo8ZsfS//wKpU7ZY"
)


def verify_password_dummy(password: str) -> None:
    """Burn one password-verification's worth of time, then always fail.

    Call this on the "account does not exist" branch of login so the branch is
    timing-indistinguishable from a real failed login. Returns nothing — the
    caller is expected to raise the same generic error either way.
    """
    verify_password(password, _DUMMY_HASH)


# --- Async wrappers (what routers must call) --------------------------------
# Argon2 is *deliberately* expensive (~33 ms at our parameters). Running it
# inline blocks the event loop for the whole request, so a burst of logins
# serialises every other request behind them.
#
# Offloading to a worker thread fixes that, and it is not just about fairness:
# argon2 releases the GIL, so concurrent hashes genuinely run in parallel
# (measured ~2.3x throughput on this machine).
#
# The sync primitives above stay public because CLI scripts (app/seed.py) have
# no event loop to protect.
async def hash_password_async(password: str) -> str:
    return await to_thread.run_sync(hash_password, password)


async def verify_password_async(password: str, hashed: str) -> bool:
    return await to_thread.run_sync(verify_password, password, hashed)


async def verify_password_dummy_async(password: str) -> None:
    """Async twin of `verify_password_dummy` — see its docstring for the why."""
    await to_thread.run_sync(verify_password_dummy, password)


# --- JWT -------------------------------------------------------------------
TokenType = Literal["access", "refresh"]


def _signing_key() -> str:
    if settings.JWT_ALGORITHM.upper().startswith("RS"):
        return settings.jwt_private_key
    return settings.SECRET_KEY


def _verify_key() -> str:
    if settings.JWT_ALGORITHM.upper().startswith("RS"):
        return settings.jwt_public_key
    return settings.SECRET_KEY


def _create_token(
    *,
    subject: str,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,          # user_id
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if extra_claims:
        # Only non-PII claims allowed (profile_id / role).
        payload.update(extra_claims)
    return jwt.encode(payload, _signing_key(), algorithm=settings.JWT_ALGORITHM)


def create_access_token(
    *, user_id: str, role: str = "user", profile_id: str | None = None
) -> str:
    claims = {"role": role}
    if profile_id:
        claims["profile_id"] = profile_id
    return _create_token(
        subject=str(user_id),
        token_type="access",
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        extra_claims=claims,
    )


def create_refresh_token(*, user_id: str, epoch: int = 0) -> str:
    """Issue a refresh token bound to the user's current token epoch.

    The epoch lets the server invalidate every outstanding refresh token for a
    user in one operation (see `services.token_store`).
    """
    return _create_token(
        subject=str(user_id),
        token_type="refresh",
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        extra_claims={"epoch": epoch},
    )


def token_remaining_seconds(payload: dict[str, Any]) -> int:
    """Seconds until `exp`, floored at 0 — used as the deny-list TTL."""
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return 0
    remaining = int(exp - datetime.now(timezone.utc).timestamp())
    return max(remaining, 0)


def decode_token(token: str, *, expected_type: TokenType | None = None) -> dict[str, Any]:
    """Decode & validate a JWT. Raises JWTError on any problem."""
    payload = jwt.decode(
        token,
        _verify_key(),
        algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["exp", "sub", "type"]},
    )
    if expected_type and payload.get("type") != expected_type:
        raise JWTError(f"Unexpected token type: {payload.get('type')}")
    return payload
