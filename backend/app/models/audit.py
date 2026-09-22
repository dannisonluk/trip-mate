"""Append-only audit trail for security-relevant actions.

Design notes
------------
* **Append-only, structurally.** There is no `updated_at`, no `UPDATE` path and
  no `DELETE` path anywhere in the application. An audit row that can be edited
  is not evidence, it is a comment — so the model does not even offer the
  columns an edit would need. Rows disappear only when the database itself is
  dropped.

* **`actor_profile_id` is `SET NULL`, never `CASCADE`.** The same reasoning as
  `notifications.actor_profile_id`, but the consequence is sharper here: if
  erasing a user also erased the record of *what they did*, the right to erasure
  would double as a way to launder an abuse history. After a hard delete the
  entry survives with a null actor — the event is retained, the identity is not.
  For the anonymize path the profile row remains, so the actor is still
  resolvable to "a deactivated user".

* **`metadata` is a reserved attribute name on a declarative class**, so the
  JSON column is exposed as `detail`. Naming it `metadata` raises
  `InvalidRequestError` at mapper configuration time, not at import — an
  expensive way to learn a naming rule.

* **`detail` must never carry PII.** Ids, enum values and counts only. The audit
  trail is the one table that intentionally outlives an erasure request, so
  anything free-text written here becomes a permanent copy of data the user
  asked to have removed.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin, utcnow
from app.models.enums import AuditAction, enum_col

#: `AuditLog` has no `updated_at`, so it cannot use `TimestampMixin` — that is
#: the point of the table, not an oversight.


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        # "What has this account done?" — the question an abuse investigation asks.
        Index("ix_audit_logs_actor_created", "actor_profile_id", "created_at"),
        # "Show me every block in the last week" — the question a review asks.
        Index("ix_audit_logs_action_created", "action", "created_at"),
    )

    # Who acted. SET NULL → the record outlives the actor (see module docstring).
    actor_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True
    )

    action: Mapped[AuditAction] = mapped_column(
        enum_col(AuditAction, "audit_action"), nullable=False
    )

    # Loose pointer to the subject. Not an FK: the target is frequently a row
    # that the audited action deletes (a block that was removed, a user that was
    # erased), and a real FK would either cascade the evidence away or refuse the
    # delete. `target_type` says which table `target_id` belongs to.
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    # Structured context: ids, enum values, counts. Never free text, never PII.
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Ties the entry to the access-log line and the client's `X-Request-ID`, so
    # "what else happened during this request" is one search.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Python-side default (microsecond precision) + server_default for raw SQL.
    # Same reasoning as `TimestampMixin`: SQLite's CURRENT_TIMESTAMP is
    # second-precision, which makes an audit trail's ordering ambiguous exactly
    # when several events share a second — which is when you need it most.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditLog {self.action} actor={self.actor_profile_id} at={self.created_at}>"
