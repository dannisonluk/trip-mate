"""Profile + travel history models (spec-aligned).

Relationships use `lazy="selectin"` so they are always eagerly loaded — this is
the key discipline that makes the async ORM safe (lazy IO would raise
MissingGreenlet inside an async request).
"""
import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import BudgetType, Gender, enum_col

if TYPE_CHECKING:
    from app.models.trip import TripPost
    from app.models.user import User


class Profile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )

    nickname: Mapped[str] = mapped_column(String(60), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 16-type indicator, stored as a 4-letter string e.g. "ENFP".
    mbti: Mapped[str | None] = mapped_column(String(4), nullable=True)

    # e.g. ["BACKPACKER", "PHOTOGRAPHY", "FOODIE"]
    travel_style_tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    languages: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    gender: Mapped[Gender | None] = mapped_column(enum_col(Gender, "gender"), nullable=True)

    user: Mapped["User"] = relationship(back_populates="profile", lazy="raise")
    travel_histories: Mapped[list["TravelHistory"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", lazy="selectin",
        passive_deletes=True, order_by="desc(TravelHistory.start_date)",
    )
    trip_posts: Mapped[list["TripPost"]] = relationship(
        back_populates="creator", cascade="all, delete-orphan", lazy="raise",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Profile {self.id} nickname={self.nickname!r}>"


class TravelHistory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A past trip the user has taken — the main signal for companion matching."""

    __tablename__ = "travel_histories"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )

    country: Mapped[str] = mapped_column(String(80), nullable=False)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    budget_type: Mapped[BudgetType] = mapped_column(
        enum_col(BudgetType, "budget_type"), default=BudgetType.MODERATE, nullable=False
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_urls: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    # Coarse-location + privacy control (Security Spec §2.2).
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    profile: Mapped["Profile"] = relationship(back_populates="travel_histories", lazy="raise")
