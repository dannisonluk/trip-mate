"""Review router — post-trip ratings between travellers.

Spec endpoints:
  POST /api/v1/reviews
  GET  /api/v1/profiles/{user_id}/reviews
"""
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.v1.profiles import resolve_profile
from app.core.deps import CurrentProfile, DbSession
from app.core.rate_limit import WRITE_RATE, limit
from app.models.enums import ApplicationStatus, NotificationType
from app.models.profile import Profile
from app.models.review import Review
from app.models.trip import TripApplication, TripPost
from app.schemas.review import ReviewCreate, ReviewOut, ReviewSummary
from app.schemas.trip import ProfileSummary
from app.services import content_filter, moderation
from app.services import notifications as notifications_service

router = APIRouter(tags=["reviews"])


async def _shared_trip_ids(db, a: uuid.UUID, b: uuid.UUID) -> set[uuid.UUID]:
    """Trip posts that both parties actually took part in.

    This is what stops the review system from being used for drive-by
    score-bombing: you can only rate someone you actually travelled with.

    It returns the *trip ids* rather than a yes/no, and that distinction is the
    whole point. A pair-level answer ("have these two ever travelled together?")
    combined with a client-supplied `trip_post_id` is satisfiable once and then
    replayable forever: the uniqueness constraint is
    `(reviewer_id, reviewee_id, trip_post_id)`, so once a pair has any shared
    trip at all, varying the trip id on each request yields unlimited reviews —
    each one attached to a trip neither party was on. The caller therefore has
    to check that the trip the review claims to be about is one of these.
    """
    stmt = (
        select(TripApplication.trip_post_id)
        .join(TripPost, TripPost.id == TripApplication.trip_post_id)
        .where(
            TripApplication.status == ApplicationStatus.ACCEPTED,
            or_(
                (TripPost.creator_id == a) & (TripApplication.applicant_id == b),
                (TripPost.creator_id == b) & (TripApplication.applicant_id == a),
            ),
        )
    )
    return {row[0] for row in (await db.execute(stmt)).all()}


@router.post("/reviews", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
@limit(WRITE_RATE)
async def create_review(
    payload: ReviewCreate, request: Request, profile: CurrentProfile, db: DbSession
):
    if payload.reviewee_id == profile.id:
        raise HTTPException(status_code=400, detail="You cannot review yourself")
    if await db.get(Profile, payload.reviewee_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    shared_trips = await _shared_trip_ids(db, profile.id, payload.reviewee_id)
    if not shared_trips:
        raise HTTPException(
            status_code=403,
            detail="You can only review travellers you have completed a trip with",
        )
    if payload.trip_post_id is not None and payload.trip_post_id not in shared_trips:
        # The pair check above is necessary but not sufficient: on its own it can
        # be satisfied once and then replayed against any trip id, because the
        # uniqueness constraint is scoped to the trip. See `_shared_trip_ids`.
        raise HTTPException(
            status_code=403,
            detail="You can only review a trip you both took part in",
        )

    # `.first()`, never `.scalar_one_or_none()`. The read is a courtesy check
    # that produces a clean 409; it is not the guard. Two rows matching here
    # (which is possible because SQL treats NULLs as distinct, so
    # `uq_review_once_per_trip` cannot cover the NULL-trip case) used to raise
    # `MultipleResultsFound` and turn a duplicate submission into a permanent
    # 500 that no request could ever clear. A pre-check also cannot see a
    # concurrent insert at all, so it must not be the only thing standing
    # between the client and a duplicate — the constraint is.
    duplicate = (
        await db.execute(
            select(Review).where(
                Review.reviewer_id == profile.id,
                Review.reviewee_id == payload.reviewee_id,
                # Spelled out rather than `== payload.trip_post_id`: SQLAlchemy
                # does rewrite `== None` to `IS NULL`, but this branch is the one
                # the database's unique constraint cannot cover (NULLs compare as
                # distinct in SQL), so it is worth being explicit that the
                # application check is the only guard here.
                Review.trip_post_id.is_(None)
                if payload.trip_post_id is None
                else Review.trip_post_id == payload.trip_post_id,
            )
        )
    ).scalars().first()
    if duplicate:
        raise HTTPException(status_code=409, detail="You have already reviewed this trip")

    await content_filter.enforce_many(field="review", comment=payload.comment)

    review = Review(
        reviewer_id=profile.id,
        reviewee_id=payload.reviewee_id,
        trip_post_id=payload.trip_post_id,
        rating=payload.rating,
        tags=payload.tags,
        comment=payload.comment,
    )
    db.add(review)

    # Notification is emitted **before** the commit, so it shares the review's
    # transaction: either both land or neither does. That is only sound because
    # a failed insert rolls the whole thing back, which is exactly why the
    # `IntegrityError` below must not silently swallow the session into a state
    # where the notification still gets committed.
    await notifications_service.create(
        db,
        recipient_profile_id=payload.reviewee_id,
        type=NotificationType.REVIEW_RECEIVED,
        code="review.received",
        params={"actor": profile.nickname, "rating": payload.rating},
        body=(payload.comment or "")[:200] or None,
        actor_profile_id=profile.id,
        trip_post_id=payload.trip_post_id,
    )

    try:
        await db.commit()
    except IntegrityError:
        # The real guard: `uq_review_once_per_trip` for a trip-scoped review, or
        # `uq_review_once_per_untripped_pair` (partial, `trip_post_id IS NULL`)
        # for the other branch. Reaching here means a concurrent request won the
        # race after our courtesy check passed — the correct answer is the same
        # 409 it would have received, not a 500.
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="You have already reviewed this trip"
        ) from None

    await db.refresh(review)

    out = ReviewOut.model_validate(review)
    out.reviewer = ProfileSummary.model_validate(profile)
    return out


@router.get("/profiles/{user_id}/reviews", response_model=list[ReviewOut])
async def list_reviews(user_id: uuid.UUID, profile: CurrentProfile, db: DbSession):
    target = await resolve_profile(db, user_id)
    if target is None or target.id in await moderation.blocked_profile_ids(db, profile.id):
        # 404 (not 403), matching `GET /profiles/{user_id}`: a blocked user must
        # not be able to confirm the blocker's existence by reading their reviews.
        raise HTTPException(status_code=404, detail="Profile not found")

    rows = (
        await db.execute(
            select(Review)
            .where(Review.reviewee_id == target.id)
            .order_by(Review.created_at.desc())
        )
    ).scalars().all()
    return [ReviewOut.model_validate(r) for r in rows]


@router.get("/profiles/{user_id}/reviews/summary", response_model=ReviewSummary)
async def review_summary(user_id: uuid.UUID, profile: CurrentProfile, db: DbSession):
    # Same resolver as `/reviews` above. These two endpoints share a path prefix,
    # so they must agree on what the id means — this one previously accepted only
    # a profile id while its sibling also accepted a user id.
    target = await resolve_profile(db, user_id)
    if target is None or target.id in await moderation.blocked_profile_ids(db, profile.id):
        raise HTTPException(status_code=404, detail="Profile not found")

    rows = (
        await db.execute(
            select(Review.rating, func.count(Review.id))
            .where(Review.reviewee_id == target.id)
            .group_by(Review.rating)
        )
    ).all()
    distribution = {str(int(rating)): int(count) for rating, count in rows}
    total = sum(distribution.values())
    weighted = sum(int(k) * v for k, v in distribution.items())
    return ReviewSummary(
        count=total,
        average_rating=round(weighted / total, 2) if total else None,
        distribution=distribution,
    )
