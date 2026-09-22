"""Profile router — view/update profile, travel histories, upload URL, blocking.

Spec endpoints:
  GET  /profiles/me                PUT   /profiles/me
  GET  /profiles/{user_id}         GET   /profiles/{user_id}/histories
  POST /profiles/me/histories      POST  /profiles/me/upload-url
"""
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from app.core.config import settings
from app.core.deps import CurrentProfile, CurrentUser, DbSession
from app.core.rate_limit import WRITE_RATE, limit
from app.models.enums import AuditAction
from app.models.moderation import Block
from app.models.profile import Profile, TravelHistory
from app.models.review import Review
from app.models.trip import TripPost
from app.schemas.moderation import BlockOut
from app.schemas.profile import (
    ProfilePrivate,
    ProfilePublic,
    ProfileStats,
    ProfileUpdate,
    TravelHistoryCreate,
    TravelHistoryOut,
)
from app.services import audit, content_filter, moderation
from app.services.storage import UploadError, presign_put

router = APIRouter(prefix="/profiles", tags=["profiles"])


async def compute_stats(db, profile_id: uuid.UUID) -> ProfileStats:
    trips_count = await db.scalar(
        select(func.count()).select_from(TripPost).where(TripPost.creator_id == profile_id)
    )
    row = (
        await db.execute(
            select(func.count(Review.id), func.avg(Review.rating)).where(
                Review.reviewee_id == profile_id
            )
        )
    ).one()
    reviews_count, average = row[0] or 0, row[1]
    return ProfileStats(
        trips_count=int(trips_count or 0),
        reviews_count=int(reviews_count or 0),
        average_rating=round(float(average), 2) if average is not None else None,
    )


def visible_histories(profile: Profile, *, include_private: bool) -> list[TravelHistoryOut]:
    return [
        TravelHistoryOut.model_validate(h)
        for h in profile.travel_histories
        if include_private or h.is_public
    ]


async def build_public(db, profile: Profile) -> ProfilePublic:
    return ProfilePublic(
        id=profile.id,
        nickname=profile.nickname,
        avatar_url=profile.avatar_url,
        bio=profile.bio,
        mbti=profile.mbti,
        travel_style_tags=profile.travel_style_tags or [],
        languages=profile.languages or [],
        gender=profile.gender,
        created_at=profile.created_at,
        stats=await compute_stats(db, profile.id),
    )


async def build_private(db, profile: Profile, user: CurrentUser) -> ProfilePrivate:
    public = await build_public(db, profile)
    return ProfilePrivate(
        **public.model_dump(),
        user_id=profile.user_id,
        is_verified=bool(user.is_verified),
        updated_at=profile.updated_at,
    )


async def resolve_profile(db, identifier: uuid.UUID) -> Profile | None:
    """Resolve by profile id first, then by owning user id.

    The spec names the path parameter `user_id`, while other resources expose
    `profile.id`; accepting both keeps every client working (both are UUIDs).
    """
    profile = await db.get(Profile, identifier)
    if profile is not None:
        return profile
    return (
        await db.execute(select(Profile).where(Profile.user_id == identifier))
    ).scalar_one_or_none()


@router.get("/me", response_model=ProfilePrivate)
async def get_my_profile(profile: CurrentProfile, user: CurrentUser, db: DbSession):
    return await build_private(db, profile, user)


@router.put("/me", response_model=ProfilePrivate)
@limit(WRITE_RATE)
async def update_my_profile(
    payload: ProfileUpdate,
    request: Request,
    profile: CurrentProfile,
    user: CurrentUser,
    db: DbSession,
):
    changes = payload.model_dump(exclude_unset=True)
    # Nickname and bio are the two free-text fields a stranger sees first, so
    # they are moderated on the way in. Only what is being written is checked.
    await content_filter.enforce_many(
        field="profile",
        nickname=changes.get("nickname"),
        bio=changes.get("bio"),
    )
    for field, value in changes.items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return await build_private(db, profile, user)


@router.post("/me/upload-url")
@limit(WRITE_RATE)
async def create_upload_url(
    request: Request, profile: CurrentProfile, content_type: str = "image/jpeg"
):
    """Return a scoped pre-signed PUT URL (S3/R2 backend only)."""
    try:
        key, url = presign_put(content_type, prefix=f"profiles/{profile.id}")
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"object_key": key, "upload_url": url, "expires_in": settings.PRESIGN_EXPIRE_SECONDS}


@router.post("/me/histories", response_model=TravelHistoryOut, status_code=status.HTTP_201_CREATED)
@limit(WRITE_RATE)
async def add_history(
    payload: TravelHistoryCreate, request: Request, profile: CurrentProfile, db: DbSession
):
    await content_filter.enforce_many(field="profile", summary=payload.summary)
    entry = TravelHistory(profile_id=profile.id, **payload.model_dump())
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return TravelHistoryOut.model_validate(entry)


@router.delete("/me/histories/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_history(entry_id: uuid.UUID, profile: CurrentProfile, db: DbSession):
    entry = await db.get(TravelHistory, entry_id)
    if entry is None or entry.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Entry not found")
    await db.delete(entry)
    await db.commit()


@router.get("/me/blocks", response_model=list[BlockOut])
async def list_blocks(profile: CurrentProfile, db: DbSession):
    rows = (
        await db.execute(select(Block).where(Block.blocker_profile_id == profile.id))
    ).scalars().all()
    return [BlockOut.model_validate(b) for b in rows]


@router.get("/{user_id}/histories", response_model=list[TravelHistoryOut])
async def list_histories(user_id: uuid.UUID, profile: CurrentProfile, db: DbSession):
    target = await resolve_profile(db, user_id)
    if target is None or target.id in await moderation.blocked_profile_ids(db, profile.id):
        raise HTTPException(status_code=404, detail="Profile not found")
    is_self = target.id == profile.id
    return visible_histories(target, include_private=is_self)


@router.get("/{user_id}", response_model=ProfilePublic)
async def get_profile(user_id: uuid.UUID, profile: CurrentProfile, db: DbSession):
    target = await resolve_profile(db, user_id)
    if target is None or target.id in await moderation.blocked_profile_ids(db, profile.id):
        # 404 (not 403) so blocked users cannot probe for existence (§1.3).
        raise HTTPException(status_code=404, detail="Profile not found")
    return await build_public(db, target)


# --- Blocking (§3.2) -------------------------------------------------------
@router.post("/{profile_id}/block", response_model=BlockOut, status_code=status.HTTP_201_CREATED)
@limit(WRITE_RATE)
async def block_profile(
    profile_id: uuid.UUID, request: Request, profile: CurrentProfile, db: DbSession
):
    if profile_id == profile.id:
        raise HTTPException(status_code=400, detail="You cannot block yourself")
    if await db.get(Profile, profile_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    block, created = await moderation.block_profile(db, profile.id, profile_id)
    # Only record an actual state change. `block_profile` is idempotent, so a
    # repeat block inserts nothing; auditing it anyway would append a second
    # USER_BLOCKED row for an event that did not happen. This is the mirror of the
    # `if removed:` guard on the unblock path below.
    if created:
        # Same transaction as the block: the audit trail records that the block
        # happened, or neither of them does.
        await audit.record(
            db,
            action=AuditAction.USER_BLOCKED,
            actor_profile_id=profile.id,
            target_type="profile",
            target_id=profile_id,
            request=request,
        )
    await db.commit()
    return BlockOut.model_validate(block)


@router.delete("/{profile_id}/block", status_code=status.HTTP_204_NO_CONTENT)
async def unblock_profile(
    profile_id: uuid.UUID, request: Request, profile: CurrentProfile, db: DbSession
):
    removed = await moderation.unblock_profile(db, profile.id, profile_id)
    # Only record an actual state change. Auditing a no-op unblock would inflate
    # the trail with events that never happened.
    if removed:
        await audit.record(
            db,
            action=AuditAction.USER_UNBLOCKED,
            actor_profile_id=profile.id,
            target_type="profile",
            target_id=profile_id,
            request=request,
        )
    await db.commit()
