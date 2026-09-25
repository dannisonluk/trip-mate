"""Moderation router — abuse reports + admin review queue (§3.2).

Every state change here writes an audit entry in the *same* transaction, because
this is the surface where "who did what, and when" has to be answerable later:
a block is a safety control, a report is an accusation, and an administrator
resolving a report is an exercise of authority over another user's account.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from app.core.deps import CurrentProfile, DbSession, require_role
from app.core.rate_limit import WRITE_RATE, limit
from app.models.enums import AuditAction, UserRole
from app.models.moderation import Report
from app.models.profile import Profile
from app.models.user import User
from app.schemas.audit import AuditLogOut, PaginatedAuditLogs
from app.schemas.moderation import ReportCreate, ReportOut
from app.services import audit

router = APIRouter(tags=["moderation"])

_REPORT_STATUSES = {"open", "reviewing", "actioned", "dismissed"}


@router.post("/reports", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
@limit(WRITE_RATE)
async def create_report(
    payload: ReportCreate, request: Request, profile: CurrentProfile, db: DbSession
):
    if payload.reported_profile_id == profile.id:
        raise HTTPException(status_code=400, detail="You cannot report yourself")
    if await db.get(Profile, payload.reported_profile_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    report = Report(
        reporter_profile_id=profile.id,
        reported_profile_id=payload.reported_profile_id,
        reason=payload.reason,
        detail=payload.detail,
    )
    db.add(report)
    await audit.record(
        db,
        action=AuditAction.REPORT_SUBMITTED,
        actor_profile_id=profile.id,
        target_type="profile",
        target_id=payload.reported_profile_id,
        # `reason` is a closed enum and therefore safe; the free-text `detail` is
        # deliberately not copied here — the audit trail outlives erasure, so it
        # must not accumulate user-written content.
        detail={"reason": payload.reason},
        request=request,
    )
    await db.commit()
    await db.refresh(report)
    return ReportOut.model_validate(report)


admin_router = APIRouter(prefix="/admin", tags=["admin"])


@admin_router.get("/reports", response_model=list[ReportOut])
async def list_reports(
    request: Request,
    admin_profile: CurrentProfile,
    db: DbSession,
    _admin: User = Depends(require_role(UserRole.ADMIN.value)),
    status_filter: str | None = None,
):
    """Admin review queue.

    Reading the queue is itself audited: access to other users' reports is an
    exercise of privilege, and "who looked at the abuse queue, and when" is a
    question an incident review needs answered.
    """
    stmt = select(Report).order_by(Report.created_at.desc())
    if status_filter:
        stmt = stmt.where(Report.status == status_filter)
    reports = (await db.execute(stmt)).scalars().all()

    await audit.record(
        db,
        action=AuditAction.ADMIN_QUEUE_VIEWED,
        actor_profile_id=admin_profile.id,
        target_type="report",
        detail={"returned": len(reports), "status_filter": status_filter},
        request=request,
    )
    await db.commit()
    return [ReportOut.model_validate(r) for r in reports]


@admin_router.patch("/reports/{report_id}", response_model=ReportOut)
@limit(WRITE_RATE)
async def update_report_status(
    report_id: uuid.UUID,
    new_status: str,
    request: Request,
    admin_profile: CurrentProfile,
    db: DbSession,
    _admin: User = Depends(require_role(UserRole.ADMIN.value)),
):
    if new_status not in _REPORT_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    previous = report.status
    report.status = new_status
    await audit.record(
        db,
        action=AuditAction.REPORT_STATUS_CHANGED,
        actor_profile_id=admin_profile.id,
        target_type="report",
        target_id=report_id,
        # The transition, not just the destination — "who moved this from open to
        # dismissed" is the question that matters, and it is unrecoverable later.
        detail={"from": previous, "to": new_status},
        request=request,
    )
    await db.commit()
    await db.refresh(report)
    return ReportOut.model_validate(report)


@admin_router.get("/audit-logs", response_model=PaginatedAuditLogs)
async def list_audit_logs(
    db: DbSession,
    _admin: User = Depends(require_role(UserRole.ADMIN.value)),
    action: AuditAction | None = None,
    actor_profile_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """Newest-first audit trail, filterable by action and actor.

    Read-only by design: there is no endpoint to edit or delete an entry, and the
    model has no columns an edit would need. An audit trail that the application
    can rewrite is not evidence.

    Reading *this* endpoint is deliberately not audited, unlike the report queue.
    A self-auditing read is self-defeating: every page view would append a row,
    the row count would grow with the act of inspecting it, and pagination would
    shift under the reader. The access log already records the request.
    """
    entries, total = await audit.list_entries(
        db,
        action=action,
        actor_profile_id=actor_profile_id,
        page=page,
        limit=limit,
    )

    actor_ids = {e.actor_profile_id for e in entries if e.actor_profile_id}
    nicknames: dict[uuid.UUID, str] = {}
    if actor_ids:
        rows = (
            await db.execute(
                select(Profile.id, Profile.nickname).where(Profile.id.in_(actor_ids))
            )
        ).all()
        nicknames = {row[0]: row[1] for row in rows}

    return PaginatedAuditLogs(
        items=[
            AuditLogOut.from_orm_entry(entry, actor_nickname=nicknames.get(entry.actor_profile_id))
            for entry in entries
        ],
        total=total,
        page=page,
        limit=limit,
    )
