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
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.deps import CurrentProfile, DbSession
from app.core.rate_limit import WRITE_RATE, limit
from app.api.v1.cities import resolve_city_id
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


async def _accepted_count(db, trip_post_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(TripApplication)
        .where(
            TripApplication.trip_post_id == trip_post_id,
            TripApplication.status == ApplicationStatus.ACCEPTED,
        )
    )
    return int(count or 0)


async def _lock_trip(db, trip_post_id: uuid.UUID) -> None:
    """Serialise writers on this trip before anything reads its capacity.

    On PostgreSQL this is `SELECT ... FOR UPDATE`. SQLite has no row locks, so
    the equivalent is a no-op write to the row, which forces SQLite to take its
    write lock up front — DEFERRED transactions otherwise start as readers and
    only upgrade at first write, so two concurrent deciders would each read the
    accepted count as it was before the other committed.

    Measured, not assumed: with only the conditional UPDATE in place (and this
    lock removed), two concurrent accepts on a `looking_for_count=1` trip both
    returned 200 and the trip ended with 2 accepted companions.
    """
    if settings.DATABASE_URL.startswith("postgresql"):
        await db.execute(select(TripPost.id).where(TripPost.id == trip_post_id).with_for_update())
    else:
        await db.execute(
            update(TripPost)
            .where(TripPost.id == trip_post_id)
            .values(looking_for_count=TripPost.looking_for_count)
        )


#: Trip lifecycle. `CANCELLED` is terminal: a cancelled trip is off the listing,
#: and reviving it would let an organiser silently resurrect a post that
#: applicants had already written off. `CLOSED` is the normal "we are full"
#: state and can be reopened.
_ALLOWED_STATUS_TRANSITIONS: dict[TripStatus, set[TripStatus]] = {
    TripStatus.OPEN: {TripStatus.CLOSED, TripStatus.CANCELLED},
    TripStatus.CLOSED: {TripStatus.OPEN, TripStatus.CANCELLED},
    TripStatus.CANCELLED: set(),
}


def _validate_status_transition(current: TripStatus, requested: TripStatus) -> None:
    """Reject a status change the lifecycle does not allow.

    Without this, `status` is a free-form field on the update schema and a
    cancelled trip comes back to life with one PATCH. Re-sending the current
    status is deliberately allowed as a no-op: a client that PATCHes a whole
    form every time should not get a 409 for a field it did not intend to move.
    """
    if requested == current:
        return
    if requested not in _ALLOWED_STATUS_TRANSITIONS[current]:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot move a trip from {current} to {requested}",
        )


async def _enforce_capacity(
    db, post: TripPost, *, pending_accept: bool = False
) -> None:
    """Refuse to leave `post` overbooked. **The only place this rule lives.**

    It is called from the two write paths that can each break the same invariant
    — accepting an application, and `PATCH /trips/{id}` lowering
    `looking_for_count` — and it exists as one function precisely because the
    second path originally had no check at all. Callers must hold the trip lock
    (see `_lock_trip`) and must have refreshed `post`, so the limit it compares
    against is the committed one rather than a stale ORM copy.

    `pending_accept` says whether the caller is about to add one more accepted
    companion. It is explicit rather than inferred because the two callers need
    different arithmetic on the same invariant, and a single formula quietly
    gets one of them wrong:

    * Accepting: the count excludes the application being accepted, whose CAS
      has not committed yet, so the prospective total is `count + 1`.
    * Lowering the limit: the accepted rows are already committed, so the
      prospective total is simply `count` — and a decrement that lands exactly
      on it (`3 -> 2` with 2 accepted) is legal, which a `>` against `count + 1`
      would have wrongly refused.
    """
    already_accepted = await _accepted_count(db, post.id)
    prospective = already_accepted + (1 if pending_accept else 0)
    if prospective > post.looking_for_count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Trip is already full "
                f"({already_accepted}/{post.looking_for_count} companions accepted)"
            ),
        )


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
    # Validated before the row is persisted: an unresolvable `city_id` would
    # otherwise be stored and never match, which is the silent failure the
    # select-only picker exists to prevent.
    post.city_id = await resolve_city_id(db, payload.city_id)
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
    """Reuse an existing 1:1 room between the pair, else create one.

    Delegates to the chat router's helper rather than reimplementing the lookup.
    The two used to be separate implementations of the same rule, and only one
    of them was reached by the explicit `POST /chat/rooms` path — so accepting an
    application and opening a chat by hand could each decide "no room exists"
    and create one, leaving the pair with two rooms holding half a conversation
    each. There is now one definition of what "the room for this pair" means.
    """
    from app.api.v1.chat import ensure_direct_room

    return await ensure_direct_room(db, profile_a, profile_b)


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

    # --- Serialise the decision on this trip --------------------------------
    # One row lock, taken **before** anything reads the state it protects, for
    # both decisions. Two separate races need it:
    #
    # (a) *Overbooking.* Two accepts for two different applications each read
    #     the accepted-count before either commits, so both see a free slot.
    #     `SELECT ... FOR UPDATE` on PostgreSQL; SQLite has no row locks, so the
    #     equivalent is a no-op write to the row, which forces SQLite to take
    #     its write lock up front (DEFERRED transactions otherwise start as
    #     readers and only upgrade at first write).
    #
    # (b) *Contradictory decisions.* Accept and reject of the **same**
    #     application both see `status == PENDING` and both proceed to the CAS
    #     below. `NullPool` gives each request its own connection, so this is a
    #     real cross-connection race, and the last writer's value sticks.
    #     Measured: the row flipped ACCEPTED → REJECTED with both requests
    #     satisfied, i.e. the accept was silently not durable.
    #
    # Taking it unconditionally is what makes (b) unreachable — gating the lock
    # on `decision == ACCEPTED` leaves reject unguarded, which is exactly how
    # the replay flipped a settled row.
    await _lock_trip(db, post.id)

    if decision == ApplicationStatus.ACCEPTED:
        # --- Capacity check, and why the order matters (#21) ----------------
        # The naive form is
        #
        #     count  = SELECT COUNT(*) WHERE status='ACCEPTED'   -- read
        #     if count < looking_for_count: UPDATE ...           -- write
        #
        # which is a time-of-check-to-time-of-use race: two owners (or one owner
        # double-clicking) both read "1 of 2 accepted", both write, and the trip
        # silently ends up with 3 companions. Nothing raises — the overbooking is
        # only visible by counting.
        #
        # Two things close (a), and **both are needed**:
        #
        # 1. **Serialise the deciding transactions.** The row lock taken above
        #    (via `_lock_trip`) makes the capacity read happen under mutual
        #    exclusion: in SQLite's default DEFERRED mode the transaction starts
        #    as a reader and only upgrades at first write, so without it a second
        #    decider would still read the count as it was before the first
        #    committed.
        #
        #    Measured, not assumed: with only the CAS (2) in place and the lock
        #    removed, two concurrent accepts on a `looking_for_count=1` trip both
        #    returned 200 and the trip ended with 2 accepted companions.
        #
        # 2. **Claim the application with a conditional UPDATE.** The status
        #    transition is `UPDATE ... WHERE status='PENDING'` and the rowcount is
        #    the decision. Exactly one caller's UPDATE matches; everyone else sees
        #    0 rows and loses cleanly, so a double-click cannot produce two
        #    ACCEPTED transitions on the same application.
        #
        # The two are not substitutes. The CAS stops *the same* application being
        # decided twice; the lock stops *two different* applications being
        # accepted past the capacity. The single-threaded suite passes with either
        # one removed, which is why this needed a real concurrency probe.
        #
        # `post.looking_for_count` is refreshed first: `_lock_trip` issues a Core
        # UPDATE, so the ORM's copy of the row is not what the database now
        # holds. Comparing a freshly-counted total against a stale limit is the
        # same class of bug one level down.
        await db.refresh(post)
        # `pending_accept=True`: the application being decided is still PENDING
        # until the CAS below, so it is not in the count yet.
        await _enforce_capacity(db, post, pending_accept=True)

    # Compare-and-set the status. `rowcount` is the arbiter: 1 means this caller
    # won the transition, 0 means somebody else already settled this application
    # (or it was never PENDING to begin with), in which case the read above was
    # taken from a stale snapshot and must not be acted on.
    claimed = await db.execute(
        update(TripApplication)
        .where(
            TripApplication.id == application.id,
            TripApplication.status == ApplicationStatus.PENDING,
        )
        .values(status=decision)
    )
    if claimed.rowcount != 1:
        await db.rollback()
        # Re-read what actually won, so a retried request reports the same thing
        # a fresh request would (idempotent same-decision, 409 on a conflict).
        settled = await db.get(TripApplication, application_id)
        if settled is not None and settled.status == decision:
            out = TripApplicationOut.model_validate(settled)
            if settled.applicant:
                out.applicant = ProfileSummary.model_validate(settled.applicant)
            return out
        current = settled.status if settled is not None else "UNKNOWN"
        raise HTTPException(
            status_code=409,
            detail=f"Application already {current}; a decision is final",
        )

    # The ORM object is now stale (the UPDATE bypassed it). Refresh so the
    # response and the notification below reflect the committed value.
    await db.refresh(application)
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
        # A code, not a sentence: this row is read by whoever holds it, in
        # whatever language they are using at the time. See B6.
        code="trip.application_accepted" if accepted else "trip.application_rejected",
        params={"actor": profile.nickname, "trip": post.title},
        body="You can start talking now." if accepted else None,
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

    # --- The capacity rule applies to *every* writer of `looking_for_count` ---
    # Accepting an application is not the only way a trip becomes overbooked:
    # this endpoint can lower the limit underneath an already-accepted group.
    # Measured before the fix: `looking_for_count 3 -> 1` on a trip with 2
    # ACCEPTED applications returned 200 and left the trip permanently
    # overbooked — and no endpoint could repair it, because raising the limit
    # back up was the only move available and the damage was already recorded in
    # the accepted rows.
    #
    # The lock and the refresh mirror `decide_application`'s ordering, and for
    # the same reason: a count read before the lock describes a snapshot that
    # another decider may already have changed.
    if "looking_for_count" in changes:
        await _lock_trip(db, trip_id)
        await db.refresh(post)
        setattr(post, "looking_for_count", changes["looking_for_count"])
        await _enforce_capacity(db, post)

    if "status" in changes:
        _validate_status_transition(post.status, changes["status"])

    for field, value in changes.items():
        setattr(post, field, value)
    await db.commit()
    await db.refresh(post)
    return _to_out(post)


@router.delete("/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
@limit(WRITE_RATE)
async def delete_trip(
    trip_id: uuid.UUID, request: Request, profile: CurrentProfile, db: DbSession
):
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
        code="trip.application_received",
        params={"actor": profile.nickname, "trip": post.title},
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
