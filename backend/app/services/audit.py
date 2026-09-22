"""Audit-trail writes.

One entry point, `record()`, used by every call site, so the three things that
are easy to get wrong are impossible to get wrong per-site:

1. **The audit row shares the caller's transaction.** Like
   `services/notifications.create`, this does not commit. An audit entry for an
   action that then rolled back would be a false record — worse than a missing
   one, because it is evidence that never happened.

2. **`detail` is restricted to ids, enums and counts.** Free text is where PII
   leaks into the one table that deliberately outlives an erasure request. The
   sanitiser is not a security boundary (a determined caller can pass anything),
   it is a tripwire — and a tripwire that fires silently is not a tripwire, so a
   dropped key is logged.

3. **Request metadata is captured consistently.** IP and user-agent come from
   `core/request_meta`, the same helper the access log uses, so the log line and
   the audit row can never disagree about who the caller was.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.logging import get_request_id
from app.core.request_meta import client_ip, user_agent
from app.models.audit import AuditLog
from app.models.enums import AuditAction

logger = logging.getLogger(__name__)

#: Detail values that are not ids/enums/numbers are dropped. Keys whose name
#: looks like it carries free text are refused outright, because those are the
#: ones that end up holding a nickname, a report body or a message preview.
#:
#: `"reason"` is deliberately *absent*. It was in this list and that was a bug:
#: `moderation.create_report` records `detail={"reason": payload.reason}`, where
#: `reason` is a closed enum enforced by `ReportCreate._valid_reason`
#: (harassment/spam/scam/inappropriate/other). The hint matched the key name,
#: dropped the value, and the audit entry for every report silently lost the one
#: field that says what the report was *about* — while the genuinely free-text
#: sibling (`Report.detail`, max 2000 chars) is never copied here at all. A
#: key-name blocklist cannot tell a closed enum from prose; logging the drop is
#: what makes the next such collision visible instead of silent.
_FORBIDDEN_DETAIL_HINTS = (
    "nickname",
    "name",
    "email",
    "phone",
    "message",
    "detail",
    "body",
    "content",
    "bio",
    "text",
    "comment",
    "review",
    "summary",
    "description",
    "note",
    "address",
)
_MAX_DETAIL_KEYS = 20


def _sanitise_detail(detail: dict | None) -> dict | None:
    if not detail:
        return None
    cleaned: dict = {}
    for key, value in list(detail.items())[:_MAX_DETAIL_KEYS]:
        if any(hint in key.lower() for hint in _FORBIDDEN_DETAIL_HINTS):
            # Dropped rather than redacted: recording that a key existed is
            # itself a hint about the content. Logged because a silent drop is
            # indistinguishable from a caller that never passed the value.
            logger.warning(
                "audit detail key %r dropped: name matches a free-text hint; "
                "pass ids, enums or counts instead",
                key,
            )
            continue
        cleaned[key] = value if isinstance(value, (str, int, float, bool)) or value is None else str(value)
    return cleaned or None


async def record(
    db: AsyncSession,
    *,
    action: AuditAction,
    actor_profile_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    detail: dict | None = None,
    request: Request | None = None,
) -> AuditLog:
    """Append one audit entry. Does **not** commit — the caller owns the transaction."""
    entry = AuditLog(
        actor_profile_id=actor_profile_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=_sanitise_detail(detail),
        request_id=get_request_id(),
        ip_address=client_ip(request) if request is not None else None,
        user_agent=user_agent(request) if request is not None else None,
    )
    db.add(entry)
    # Counted here rather than at each call site so the metric cannot drift away
    # from what is actually written.
    metrics.observe_audit_event(action.value)
    return entry


async def list_entries(
    db: AsyncSession,
    *,
    action: AuditAction | None = None,
    actor_profile_id: uuid.UUID | None = None,
    page: int = 1,
    limit: int = 50,
) -> tuple[list[AuditLog], int]:
    """Newest-first page of entries, plus the true total for pagination."""
    conditions = []
    if action is not None:
        conditions.append(AuditLog.action == action)
    if actor_profile_id is not None:
        conditions.append(AuditLog.actor_profile_id == actor_profile_id)

    count_stmt = select(func.count()).select_from(AuditLog)
    stmt = select(AuditLog)
    for condition in conditions:
        count_stmt = count_stmt.where(condition)
        stmt = stmt.where(condition)

    total = await db.scalar(count_stmt) or 0
    rows = (
        await db.execute(
            stmt.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).scalars().all()
    return list(rows), int(total)
