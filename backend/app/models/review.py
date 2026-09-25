"""Review model — post-trip ratings between travellers (spec: reviews)."""
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, JSON, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.profile import Profile


class Review(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A rating left by one traveller about another after travelling together."""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint(
            "reviewer_id", "reviewee_id", "trip_post_id", name="uq_review_once_per_trip"
        ),
        # The constraint above cannot fire when `trip_post_id IS NULL`: SQL treats
        # NULLs as distinct, so `(a, b, NULL)` never collides with another
        # `(a, b, NULL)` and a pair could accumulate unlimited untripped reviews.
        # This partial index closes that hole for exactly the rows the unique
        # constraint cannot see.
        Index(
            "uq_review_once_per_untripped_pair",
            "reviewer_id",
            "reviewee_id",
            unique=True,
            postgresql_where=text("trip_post_id IS NULL"),
            sqlite_where=text("trip_post_id IS NULL"),
        ),
    )

    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    reviewee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    trip_post_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trip_posts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1..5
    # e.g. ["PUNCTUAL", "FRIENDLY", "GOOD_PLANNER"]
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    reviewer: Mapped["Profile"] = relationship(
        "Profile", foreign_keys=[reviewer_id], lazy="selectin"
    )
    reviewee: Mapped["Profile"] = relationship(
        "Profile", foreign_keys=[reviewee_id], lazy="selectin"
    )
