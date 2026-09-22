"""Audit-trail schemas.

The response shape is deliberately *narrower* than the table: `ip_address` and
`user_agent` are stored but not exposed over the API. They exist for incident
investigation at the database level; publishing every user's address to whoever
holds an admin session is a privacy cost with no operational benefit.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.audit import AuditLog
from app.models.enums import AuditAction


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action: AuditAction
    actor_profile_id: uuid.UUID | None = None
    # Resolved server-side so the admin UI does not need one request per row, and
    # so an erased actor still renders as something meaningful.
    actor_nickname: str | None = None
    target_type: str | None = None
    target_id: uuid.UUID | None = None
    detail: dict | None = None
    request_id: str | None = None
    created_at: datetime

    @classmethod
    def from_orm_entry(cls, entry: AuditLog, *, actor_nickname: str | None = None) -> "AuditLogOut":
        """`model_validate` plus the joined nickname.

        A separate constructor rather than a computed field because the nickname
        comes from a batched lookup over the whole page, not from the row.
        """
        out = cls.model_validate(entry)
        out.actor_nickname = actor_nickname
        return out


class PaginatedAuditLogs(BaseModel):
    items: list[AuditLogOut]
    total: int
    page: int
    limit: int
