"""User account router — PDPO right to erasure (§2.1)."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select, update

from app.core.deps import CurrentProfile, CurrentUser, DbSession
from app.core.rate_limit import WRITE_RATE, limit
from app.core.security import hash_password_async, verify_password_async
from app.models.chat import ChatMessage, ChatRoomMember
from app.models.enums import AuditAction
from app.models.profile import Profile
from app.models.trip import TripApplication, TripPost
from app.schemas.auth import AccountDeleteRequest
from app.services import audit

router = APIRouter(prefix="/users", tags=["users"])


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
@limit(WRITE_RATE)
async def delete_my_account(
    payload: AccountDeleteRequest,
    request: Request,
    user: CurrentUser,
    profile: CurrentProfile,
    db: DbSession,
):
    """Erase or anonymize the caller's account.

    mode="hard_delete"  → PII rows are removed entirely (irreversible).
    mode="anonymize"    → PII is stripped and a ghost identity replaces the user,
                          while aggregate content (trip posts) is retained.

    Both paths write an audit entry. It is the one case where the audit row is
    deliberately written *before* the delete, so that `ON DELETE SET NULL` on
    `audit_logs.actor_profile_id` can do its job: the record of "an account was
    erased" survives, while the link to the erased identity does not. That is the
    only consistent reading of the right to erasure — a trail that vanished with
    the user would be useless for the abuse cases that matter, and one that kept
    the identity would defeat the erasure.
    """
    if not await verify_password_async(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    await audit.record(
        db,
        action=(
            AuditAction.ACCOUNT_DELETED
            if payload.mode == "hard_delete"
            else AuditAction.ACCOUNT_ANONYMIZED
        ),
        actor_profile_id=profile.id,
        target_type="user",
        target_id=user.id,
        detail={"mode": payload.mode},
        request=request,
    )

    if payload.mode == "hard_delete":
        # One transaction: the audit row and the deletion commit together, so the
        # trail cannot claim an erasure that rolled back.
        await db.delete(user)  # DB-level ON DELETE CASCADE clears the rest
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # --- Anonymization -----------------------------------------------------
    ghost = uuid.uuid4().hex
    user.phone_number = f"+8520000{int(ghost[:4], 16) % 10000:04d}"
    user.password_hash = await hash_password_async(uuid.uuid4().hex + "Aa1")
    user.is_active = False
    user.is_anonymized = True
    user.is_verified = False
    user.deleted_at = datetime.now(timezone.utc)

    profile.nickname = "已註銷用戶"
    profile.bio = None
    profile.avatar_url = None
    profile.mbti = None
    profile.gender = None
    profile.travel_style_tags = []
    profile.languages = []

    # Scrub free-text content authored by this user.
    for entry in profile.travel_histories:
        entry.summary = None
        entry.photo_urls = []
        entry.is_public = False

    await db.execute(
        update(ChatMessage)
        .where(ChatMessage.sender_id == profile.id)
        .values(content="[deleted]", is_deleted=True, sender_id=None)
    )
    for post in (
        await db.execute(select(TripPost).where(TripPost.creator_id == profile.id))
    ).scalars():
        post.description = "[deleted]"

    for application in (
        await db.execute(
            select(TripApplication).where(TripApplication.applicant_id == profile.id)
        )
    ).scalars():
        application.message = None

    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me/rooms")
async def my_room_ids(profile: CurrentProfile, db: DbSession):
    rows = (
        await db.execute(
            select(ChatRoomMember.room_id).where(ChatRoomMember.profile_id == profile.id)
        )
    ).scalars().all()
    return {"room_ids": [str(r) for r in rows]}
