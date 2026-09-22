"""Trip post router — browse/filter, create, detail, apply, decide.

Spec endpoints:
  GET  /trips?country&city&budget_type&tags&start_date&page&limit
  POST /trips
  GET  /trips/{trip_id}
  POST /trips/{trip_id}/apply
  PATCH /trips/applications/{application_id}
"""
import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.core.deps import CurrentProfile, DbSession
from app.core.rate_limit import WRITE_RATE, limit
from app.models.chat import ChatRoom, ChatRoomMember
from app.models.enums import ApplicationStatus, BudgetType, NotificationType, TripStatus
from app.models.trip import TripApplication, TripPost, TripPostTag, normalise_tags
from app.services import notifications as notifications_service
from app.schemas.trip import (
    PaginatedTrips,
    ProfileSummary,
    RecommendationOut,
    TripApplicationCreate,
    TripApplicationOut,
    TripPostCreate,
    TripPostDetail,
    TripPostOut,
    TripPostUpdate,
)
from app.services import content_filter, moderation
from app.services.matching import recommend_trips

router = APIRouter(prefix="/trips", tags=["trips"])


def _to_out(post: TripPost) -> TripPostOut:
    out = TripPostOut.model_validate(post)
    if post.creator:
        out.creator = ProfileSummary.model_validate(post.creator)
    return out


@router.post("", response_model=TripPostOut, status_code=status.HTTP_201_CREATED)
@limit(WRITE_RATE)
async def create_trip(
    payload: TripPostCreate, request: Request, profile: CurrentProfile, db: DbSession
):
    # Moderated before the row is built, so a rejected trip never exists even
    # transiently in the session.
    await content_filter.enforce_many(
        field="trip", title=payload.title, description=payload.description
    )

    post = TripPost(creator_id=profile.id, **payload.model_dump())
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return _to_out(post)


@router.get("", response_model=PaginatedTrips)
async def list_trips(
    db: DbSession,
    profile: CurrentProfile,
    country: str | None = None,
    city: str | None = None,
    budget_type: BudgetType | None = None,
    tags: str | None = Query(default=None, description="Comma-separated tag list"),
    start_date: date | None = Query(default=None, description="Departing on/after this date"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    hidden = await moderation.blocked_profile_ids(db, profile.id)

    stmt = select(TripPost).where(TripPost.status == TripStatus.OPEN)
    if country:
        stmt = stmt.where(func.lower(TripPost.destination_country) == country.lower())
    if city:
        stmt = stmt.where(func.lower(TripPost.destination_city) == city.lower())
    if budget_type:
        stmt = stmt.where(TripPost.budget_type == budget_type)
    if start_date:
        stmt = stmt.where(TripPost.start_date.is_not(None), TripPost.start_date >= start_date)
    if hidden:
        stmt = stmt.where(TripPost.creator_id.notin_(hidden))

    wanted_tags = normalise_tags((tags or "").split(","))

    if wanted_tags:
        # Exact, unbounded and index-driven. The previous implementation loaded at
        # most `_TAG_SCAN_LIMIT` (1000) rows and filtered them in Python, because
        # JSON containment is spelled differently on SQLite and PostgreSQL — so on
        # a larger dataset it silently dropped matches *and* reported a `total`
        # that was only correct inside the scan window.
        #
        # Semantics are unchanged: any overlap matches (OR, not AND), and `IN`
        # naturally counts a post once even when several of its tags match.
        #
        # Why a semi-join and not a correlated `EXISTS`: SQLite cannot reorder a
        # correlated EXISTS, so it evaluates one index seek per row of
        # `trip_posts` — a full scan no matter how rare the tag is. Measured at
        # 50k posts / 50k tag rows (best of 3):
        #
        #     selective tag (50 matches)   EXISTS 57.9 ms   IN  0.1 ms
        #     non-selective (50k matches)  EXISTS 57.0 ms   IN  159 ms
        #
        # Filtering by tag exists to *narrow* a result set, so the selective case
        # is the one worth optimising; the degenerate "every post carries this
        # tag" case is still well under a fifth of a second at 50k rows. The
        # trade-off is that `IN` materialises the matching tag list, where EXISTS
        # is flat in memory — revisit if `trip_post_tags` ever grows past a few
        # hundred thousand rows for a single tag.
        stmt = stmt.where(
            TripPost.id.in_(
                select(TripPostTag.trip_post_id).where(TripPostTag.tag.in_(wanted_tags))
            )
        )

    # One pagination path for both cases, so the tag filter can no longer drift
    # away from the unfiltered behaviour.
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page_items = (
        await db.execute(
            stmt.order_by(TripPost.created_at.desc()).offset((page - 1) * limit).limit(limit)
        )
    ).scalars().all()

    return PaginatedTrips(
        items=[_to_out(p) for p in page_items], total=int(total), page=page, limit=limit
    )


@router.get("/recommendations", response_model=list[RecommendationOut])
async def recommendations(
    profile: CurrentProfile, db: DbSession, limit_: int = Query(20, alias="limit", ge=1, le=50)
):
    recs = await recommend_trips(db, profile, limit=limit_)
    return [
        RecommendationOut(post=_to_out(r.post), score=r.score, reasons=r.reasons) for r in recs
    ]


@router.get("/mine", response_model=list[TripPostOut])
async def my_trips(profile: CurrentProfile, db: DbSession):
    posts = (
        await db.execute(
            select(TripPost)
            .where(TripPost.creator_id == profile.id)
            .order_by(TripPost.created_at.desc())
        )
    ).scalars().all()
    return [_to_out(p) for p in posts]


async def _ensure_direct_room(db, profile_a: uuid.UUID, profile_b: uuid.UUID) -> ChatRoom:
    """Reuse an existing 1:1 room between the pair, else create one."""
    candidates = (
        await db.execute(
            select(ChatRoom)
            .join(ChatRoomMember, ChatRoomMember.room_id == ChatRoom.id)
            .where(ChatRoom.room_type == "DIRECT", ChatRoomMember.profile_id == profile_a)
        )
    ).scalars().unique().all()

    for room in candidates:
        if {m.profile_id for m in room.members} == {profile_a, profile_b}:
            return room

    room = ChatRoom(room_type="DIRECT")
    db.add(room)
    await db.flush()
    db.add_all(
        [
            ChatRoomMember(room_id=room.id, profile_id=profile_a),
            ChatRoomMember(room_id=room.id, profile_id=profile_b),
        ]
    )
    await db.flush()
    return room


@router.patch("/applications/{application_id}", response_model=TripApplicationOut)
@limit(WRITE_RATE)
async def decide_application(
    application_id: uuid.UUID,
    request: Request,
    decision: ApplicationStatus,
    profile: CurrentProfile,
    db: DbSession,
):
    """Accept or reject an application. Only the trip creator may decide.

    A decision is **final**: an application that is already ACCEPTED or REJECTED
    cannot be re-decided. Without this guard the update is a plain overwrite, so
    a second PATCH would flip a settled REJECTED back to ACCEPTED (and emit a
    second, contradictory notification) — the reject would not be durable.
    """
    if decision not in (ApplicationStatus.ACCEPTED, ApplicationStatus.REJECTED):
        raise HTTPException(status_code=400, detail="decision must be ACCEPTED or REJECTED")

    application = await db.get(TripApplication, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")

    # Idempotent replay of the *same* decision is a no-op; a *different* decision
    # is a conflict. Both are rejections of the write, but they mean different
    # things to the caller, and a retried request must not look like an error.
    if application.status != ApplicationStatus.PENDING:
        if application.status == decision:
            out = TripApplicationOut.model_validate(application)
            if application.applicant:
                out.applicant = ProfileSummary.model_validate(application.applicant)
            return out
        raise HTTPException(
            status_code=409,
            detail=f"Application already {application.status}; a decision is final",
        )


    post = await db.get(TripPost, application.trip_post_id)
    if post is None or post.creator_id != profile.id:
        raise HTTPException(status_code=404, detail="Application not found")

    # `looking_for_count` was previously stored and validated but never consulted,
    # so a creator could accept far more companions than the trip advertised.
    # Checked before mutating so a rejected accept leaves no state behind.
    if decision == ApplicationStatus.ACCEPTED:
        already_accepted = await db.scalar(
            select(func.count())
            .select_from(TripApplication)
            .where(
                TripApplication.trip_post_id == post.id,
                TripApplication.status == ApplicationStatus.ACCEPTED,
            )
        )
        if (already_accepted or 0) >= post.looking_for_count:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Trip is already full "
                    f"({already_accepted}/{post.looking_for_count} companions accepted)"
                ),
            )

    application.status = decision

    if decision == ApplicationStatus.ACCEPTED:
        await _ensure_direct_room(db, profile.id, application.applicant_id)

    accepted = decision == ApplicationStatus.ACCEPTED
    await notifications_service.create(
        db,
        recipient_profile_id=application.applicant_id,
        type=(
            NotificationType.APPLICATION_ACCEPTED
            if accepted
            else NotificationType.APPLICATION_REJECTED
        ),
        title=(
            f"{profile.nickname} 接受了你的申請：{post.title}"
            if accepted
            else f"{profile.nickname} 婉拒了你的申請：{post.title}"
        ),
        body="你們現在可以開始對話了。" if accepted else None,
        actor_profile_id=profile.id,
        trip_post_id=post.id,
    )

    await db.commit()
    await db.refresh(application)
    out = TripApplicationOut.model_validate(application)
    if application.applicant:
        out.applicant = ProfileSummary.model_validate(application.applicant)
    return out


@router.get("/{trip_id}", response_model=TripPostDetail)
async def get_trip(trip_id: uuid.UUID, profile: CurrentProfile, db: DbSession):
    post = await db.get(TripPost, trip_id)
    if post is None or post.creator_id in await moderation.blocked_profile_ids(db, profile.id):
        raise HTTPException(status_code=404, detail="Trip not found")

    # Build the detail from the relationship-free view.
    #
    # `TripPost.applications` is `lazy="raise"` and `db.get()` does not eager-load
    # it, so `TripPostDetail.model_validate(post)` raises InvalidRequestError
    # ("not available due to lazy='raise'") and the endpoint returns 500 — for
    # every viewer, including the organiser.
    #
    # Going via `TripPostOut` also makes the privacy rule structural: the
    # applicant list starts empty and can only ever be filled in for the
    # organiser below, so it cannot leak by accident.
    detail = TripPostDetail(**TripPostOut.model_validate(post).model_dump())

    if post.creator_id == profile.id:
        rows = (
            await db.execute(
                select(TripApplication)
                .where(TripApplication.trip_post_id == trip_id)
                .order_by(TripApplication.created_at.desc())
            )
        ).scalars().all()
        detail.applications = [TripApplicationOut.model_validate(a) for a in rows]
    return detail


@router.patch("/{trip_id}", response_model=TripPostOut)
@limit(WRITE_RATE)
async def update_trip(
    trip_id: uuid.UUID,
    payload: TripPostUpdate,
    request: Request,
    profile: CurrentProfile,
    db: DbSession,
):
    post = await db.get(TripPost, trip_id)
    if post is None or post.creator_id != profile.id:
        # 404 rather than 403 — do not leak the existence of others' resources (§1.3).
        raise HTTPException(status_code=404, detail="Trip not found")
    changes = payload.model_dump(exclude_unset=True)
    # Only the fields actually being written are moderated. Checking the stored
    # values too would make an unrelated edit (say, extending a date) fail
    # because of text the filter had already allowed under an older rule set.
    await content_filter.enforce_many(
        field="trip",
        title=changes.get("title"),
        description=changes.get("description"),
    )
    for field, value in changes.items():
        setattr(post, field, value)
    await db.commit()
    await db.refresh(post)
    return _to_out(post)


@router.delete("/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip(trip_id: uuid.UUID, profile: CurrentProfile, db: DbSession):
    post = await db.get(TripPost, trip_id)
    if post is None or post.creator_id != profile.id:
        raise HTTPException(status_code=404, detail="Trip not found")
    await db.delete(post)
    await db.commit()


@router.post(
    "/{trip_id}/apply", response_model=TripApplicationOut, status_code=status.HTTP_201_CREATED
)
@limit(WRITE_RATE)
async def apply_to_trip(
    trip_id: uuid.UUID,
    payload: TripApplicationCreate,
    request: Request,
    profile: CurrentProfile,
    db: DbSession,
):
    post = await db.get(TripPost, trip_id)
    if post is None or post.status != TripStatus.OPEN:
        raise HTTPException(status_code=404, detail="Trip not available")
    if post.creator_id == profile.id:
        raise HTTPException(status_code=400, detail="You cannot apply to your own trip")

    try:
        await moderation.ensure_not_blocked(db, profile.id, post.creator_id)
    except PermissionError:
        raise HTTPException(status_code=403, detail="You cannot interact with this user") from None

    existing = (
        await db.execute(
            select(TripApplication).where(
                TripApplication.trip_post_id == trip_id,
                TripApplication.applicant_id == profile.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="You have already applied to this trip")

    application = TripApplication(
        trip_post_id=trip_id, applicant_id=profile.id, message=payload.message
    )
    db.add(application)

    # Same transaction as the application itself: a notification for something
    # that failed to save would be a lie.
    await notifications_service.create(
        db,
        recipient_profile_id=post.creator_id,
        type=NotificationType.APPLICATION_RECEIVED,
        title=f"{profile.nickname} 申請加入「{post.title}」",
        body=(payload.message or "")[:200] or None,
        actor_profile_id=profile.id,
        trip_post_id=trip_id,
    )

    await db.commit()
    await db.refresh(application)

    out = TripApplicationOut.model_validate(application)
    out.applicant = ProfileSummary.model_validate(profile)
    return out


@router.get("/{trip_id}/applications", response_model=list[TripApplicationOut])
async def list_applications(trip_id: uuid.UUID, profile: CurrentProfile, db: DbSession):
    post = await db.get(TripPost, trip_id)
    if post is None or post.creator_id != profile.id:
        raise HTTPException(status_code=404, detail="Trip not found")
    rows = (
        await db.execute(
            select(TripApplication)
            .where(TripApplication.trip_post_id == trip_id)
            .order_by(TripApplication.created_at.desc())
        )
    ).scalars().all()
    return [TripApplicationOut.model_validate(a) for a in rows]
