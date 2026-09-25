"""Notification creation and fan-out.

Two invariants this module enforces so callers cannot get them wrong:

1. **Never notify yourself.** Any action that triggers a notification also
   targets the actor in some cases (e.g. a user messaging a room they are in),
   and self-notifications are pure noise.

2. **Never notify across a block.** Blocking is a safety control, and a
   notification is a delivery channel — if A blocks B, B must not be able to
   make A's notification bell ring. Callers usually check blocks already; this
   is defence in depth, because forgetting the check in one call site would
   silently turn notifications into a harassment vector.

Message notifications are *upserted per room* rather than created per message.
A busy room would otherwise generate one row per message per member; instead the
unread row is refreshed, which is both cheaper and what users expect (one badge,
not forty).
"""
from __future__ import annotations

import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import NotificationType
from app.models.moderation import Block
from app.models.notification import Notification

# Keeps a chat preview from blowing past the column width (String(300)).
_PREVIEW_LIMIT = 120


async def _blocked_between(
    db: AsyncSession, profile_a: uuid.UUID, profile_b: uuid.UUID
) -> bool:
    """True if either party has blocked the other (blocks are bidirectional)."""
    stmt = select(func.count()).select_from(Block).where(
        or_(
            and_(
                Block.blocker_profile_id == profile_a,
                Block.blocked_profile_id == profile_b,
            ),
            and_(
                Block.blocker_profile_id == profile_b,
                Block.blocked_profile_id == profile_a,
            ),
        )
    )
    return (await db.execute(stmt)).scalar_one() > 0


async def create(
    db: AsyncSession,
    *,
    recipient_profile_id: uuid.UUID,
    type: NotificationType,
    code: str,
    params: dict | None = None,
    body: str | None = None,
    actor_profile_id: uuid.UUID | None = None,
    trip_post_id: uuid.UUID | None = None,
    chat_room_id: uuid.UUID | None = None,
) -> Notification | None:
    """Create one notification. Returns None if it was suppressed.

    `code` is a language-neutral key the client resolves through its own
    dictionary; `params` carries the values to interpolate. **Never pass a
    rendered sentence as `code`** — the row outlives the request that wrote it,
    and a sentence baked in at write time reads wrong for every user whose
    locale differs from the writer's.

    Does **not** commit — the caller owns the transaction, so a notification is
    written atomically with the action that caused it. A notification for an
    application that failed to persist would be worse than no notification.
    """
    if actor_profile_id is not None:
        if actor_profile_id == recipient_profile_id:
            return None
        if await _blocked_between(db, actor_profile_id, recipient_profile_id):
            return None

    notification = Notification(
        recipient_profile_id=recipient_profile_id,
        type=type,
        code=code,
        params=params,
        body=body,
        actor_profile_id=actor_profile_id,
        trip_post_id=trip_post_id,
        chat_room_id=chat_room_id,
    )
    db.add(notification)
    return notification


async def notify_new_message(
    db: AsyncSession,
    *,
    recipient_profile_id: uuid.UUID,
    chat_room_id: uuid.UUID,
    actor_profile_id: uuid.UUID,
    actor_nickname: str,
    room_label: str | None,
    content: str,
) -> Notification | None:
    """Record/refresh the unread "new message" notification for one room.

    If an unread one already exists for this (recipient, room) pair it is
    refreshed in place, so the badge count reflects *rooms with unread messages*
    rather than message volume.

    `room_label` is the group name for TRIP rooms; pass None for DIRECT rooms,
    where naming the room after the sender would just read "Alice in Alice".

    Two codes rather than one with a conditional label, because the presence of
    the room name changes the sentence's shape ("Alice in 「Tokyo trip」" vs
    "Alice"), not just a substituted word.
    """
    if actor_profile_id == recipient_profile_id:
        return None
    if await _blocked_between(db, actor_profile_id, recipient_profile_id):
        return None

    code = "chat.new_message_in_room" if room_label else "chat.new_message"
    params = (
        {"actor": actor_nickname, "room": room_label}
        if room_label
        else {"actor": actor_nickname}
    )

    preview = content.strip()
    if len(preview) > _PREVIEW_LIMIT:
        preview = preview[: _PREVIEW_LIMIT - 1] + "…"

    existing = (
        await db.execute(
            select(Notification).where(
                Notification.recipient_profile_id == recipient_profile_id,
                Notification.chat_room_id == chat_room_id,
                Notification.type == NotificationType.NEW_MESSAGE,
                Notification.read_at.is_(None),
            )
        )
    ).scalars().first()

    if existing is not None:
        # Refresh in place: newest sender + newest preview, still one badge.
        existing.actor_profile_id = actor_profile_id
        existing.code = code
        existing.params = params
        existing.body = preview
        return existing

    return await create(
        db,
        recipient_profile_id=recipient_profile_id,
        type=NotificationType.NEW_MESSAGE,
        code=code,
        params=params,
        body=preview,
        actor_profile_id=actor_profile_id,
        chat_room_id=chat_room_id,
    )


async def unread_count(db: AsyncSession, profile_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.recipient_profile_id == profile_id,
            Notification.read_at.is_(None),
        )
    )
    return (await db.execute(stmt)).scalar_one()
