"""SQLAlchemy declarative base + shared mixins."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    """`created_at` / `updated_at` with microsecond precision.

    `server_default=func.now()` alone is not enough: on SQLite that resolves to
    `CURRENT_TIMESTAMP`, which has **second** precision. Two rows written in the
    same second then tie, and `ORDER BY created_at DESC` returns them in an
    arbitrary order — which silently breaks "newest first" feeds (notifications,
    messages, activity lists).

    Adding the Python-side `default=utcnow` makes inserts carry microseconds, so
    ties essentially cannot happen. `server_default` is kept so raw-SQL inserts
    and existing rows still get a value. This is a Python-side default only —
    no DDL change, hence no migration.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Used for accounts scheduled for anonymization (§2.1 right to erase)."""

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
