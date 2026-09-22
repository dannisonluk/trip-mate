"""Notification router.

Every query is scoped to the caller's own profile id. There is deliberately no
`GET /notifications/{id}` endpoint — a by-id lookup invites an IDOR bug, and the
client never needs one.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.core.deps import CurrentProfile, DbSession
from app.core.rate_limit import READ_RATE, WRITE_RATE, limit
from app.models.notification import Notification
from app.schemas.notification import NotificationOut, NotificationPage, UnreadCount

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationPage)
@limit(READ_RATE)
async def list_notifications(
    request: Request,
    profile: CurrentProfile,
    db: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
):
    """Newest first, with the unread badge count included.

    Returning `unread` alongside the page saves the navbar a second round-trip
    on every poll.
    """
    filters = [Notification.recipient_profile_id == profile.id]
    if unread_only:
        filters.append(Notification.read_at.is_(None))

    total = (
        await db.scalar(select(func.count()).select_from(Notification).where(*filters))
    ) or 0
    unread = (
        await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.recipient_profile_id == profile.id,
                Notification.read_at.is_(None),
            )
        )
    ) or 0

    rows = (
        await db.execute(
            select(Notification)
            .where(*filters)
            .order_by(Notification.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().unique().all()

    return NotificationPage(
        items=[NotificationOut.model_validate(r) for r in rows],
        total=total,
        unread=unread,
        page=page,
        page_size=page_size,
    )


@router.get("/unread-count", response_model=UnreadCount)
@limit(READ_RATE)
async def unread_count(request: Request, profile: CurrentProfile, db: DbSession):
    """Cheap polling endpoint for the navbar badge."""
    count = (
        await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.recipient_profile_id == profile.id,
                Notification.read_at.is_(None),
            )
        )
    ) or 0
    return UnreadCount(unread=count)


@router.post("/{notification_id}/read", response_model=NotificationOut)
@limit(WRITE_RATE)
async def mark_read(
    notification_id: uuid.UUID,
    request: Request,
    profile: CurrentProfile,
    db: DbSession,
):
    """Mark one notification read.

    Filtering by `recipient_profile_id` in the WHERE clause (rather than fetching
    then comparing) means someone else's id simply yields a 404 — the same
    response as a non-existent id, so existence is not leaked.
    """
    row = (
        await db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.recipient_profile_id == profile.id,
            )
        )
    ).scalars().unique().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")

    if row.read_at is None:
        row.read_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(row)
    return NotificationOut.model_validate(row)


@router.post("/read-all", response_model=UnreadCount)
@limit(WRITE_RATE)
async def mark_all_read(request: Request, profile: CurrentProfile, db: DbSession):
    rows = (
        await db.execute(
            select(Notification).where(
                Notification.recipient_profile_id == profile.id,
                Notification.read_at.is_(None),
            )
        )
    ).scalars().all()
    if rows:
        now = datetime.now(timezone.utc)
        for row in rows:
            row.read_at = now
        await db.commit()
    return UnreadCount(unread=0)


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
@limit(WRITE_RATE)
async def delete_notification(
    notification_id: uuid.UUID,
    request: Request,
    profile: CurrentProfile,
    db: DbSession,
):
    row = (
        await db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.recipient_profile_id == profile.id,
            )
        )
    ).scalars().unique().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    await db.delete(row)
    await db.commit()
