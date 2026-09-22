"""Refresh-token revocation: per-token deny-list + per-user epoch.

Closes the "stateless JWT cannot be revoked" gap noted in docs/SECURITY.md.

Two mechanisms, because they answer different questions:

1. **Deny-list** (`jti`) — "this exact token is no longer valid."
   Set on logout and on every rotation. Entries carry a TTL equal to the token's
   remaining lifetime, so the list stays bounded instead of growing forever.

2. **Epoch** (`user_id`) — "every token issued before now is invalid."
   Embedded in each refresh token at issue time. Bumping it invalidates the
   user's whole outstanding set in one operation, which is what makes **reuse
   detection** actionable: if a token that was already rotated away is presented
   again, one of the two holders is an attacker, and the safe response is to
   force a fresh login rather than guess which one is legitimate.

Only refresh tokens carry the epoch. Access tokens stay short-lived (15 min) and
stateless, which is the usual trade-off: revocation latency for access tokens is
bounded by their TTL.
"""
from __future__ import annotations

import logging

from app.services import kv

logger = logging.getLogger("tripmate.tokens")

_REVOKED_PREFIX = "tripmate:revoked:"
_EPOCH_PREFIX = "tripmate:epoch:"

# A refresh token lives at most this long, so an epoch entry never needs to
# outlive it. Kept slightly above REFRESH_TOKEN_EXPIRE_DAYS for safety.
_EPOCH_TTL = 60 * 60 * 24 * 31


def _revoked_key(jti: str) -> str:
    return f"{_REVOKED_PREFIX}{jti}"


def _epoch_key(user_id: str) -> str:
    return f"{_EPOCH_PREFIX}{user_id}"


async def revoke(jti: str, ttl_seconds: int) -> None:
    """Deny-list a single token id until it would have expired anyway."""
    if ttl_seconds <= 0:
        return  # already expired; nothing to remember
    await kv.set_value(_revoked_key(jti), "1", ttl_seconds)


async def is_revoked(jti: str) -> bool:
    return (await kv.get_value(_revoked_key(jti))) is not None


async def current_epoch(user_id: str) -> int:
    raw = await kv.get_value(_epoch_key(user_id))
    return int(raw) if raw is not None else 0


async def bump_epoch(user_id: str) -> int:
    """Invalidate every outstanding refresh token for this user.

    Called on reuse detection. Returns the new epoch.
    """
    new_epoch = await kv.increment(_epoch_key(user_id), _EPOCH_TTL)
    logger.warning(
        "Refresh-token reuse detected for user %s — bumped epoch to %s "
        "(all outstanding refresh tokens are now invalid).",
        user_id,
        new_epoch,
    )
    return new_epoch
