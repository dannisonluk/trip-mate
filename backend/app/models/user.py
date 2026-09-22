"""User account model — phone-based identity + PDPO consent trail.

Spec: users(phone_number unique, password_hash, is_verified, created_at)
Plus the Security & Privacy Spec additions: role, consent trail, soft delete.
"""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import UserRole, enum_col

if TYPE_CHECKING:
    from app.models.profile import Profile


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"

    # HK mobile number in E.164, e.g. +85291234567. Primary login identifier.
    phone_number: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Set to True once the SMS OTP has been verified.
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    role: Mapped[UserRole] = mapped_column(
        enum_col(UserRole, "user_role"), default=UserRole.USER, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # --- PDPO §2.1 explicit consent trail ---------------------------------
    consent_privacy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    consent_terms: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_anonymized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    profile: Mapped["Profile"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan",
        lazy="selectin", passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.id} verified={self.is_verified}>"
