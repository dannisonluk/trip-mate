"""Trip posts (companion recruitment) + applications (spec-aligned)."""
import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ApplicationStatus,
    BudgetType,
    TargetGender,
    TripStatus,
    enum_col,
)

if TYPE_CHECKING:
    from app.models.profile import Profile

#: Maximum number of tags a single trip post may carry (mirrored in the API schema).
TAG_LIMIT = 12
#: Longest accepted tag, in characters. Tags are normalised, so this is a bound
#: on the *stored* form, not on what a client may send.
TAG_MAX_LENGTH = 40


def normalise_tags(values) -> list[str]:
    """Canonical tag form: stripped, upper-cased, de-duplicated, order preserved.

    Normalising on the way *in* is what makes the join table searchable with a
    plain `tag IN (...)`: if "photography" and "PHOTOGRAPHY" were allowed to
    become two different rows, a filter could only ever match one of them, and
    the same trip would appear under one spelling and not the other.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in values or []:
        if raw is None:
            # `str(None)` is "NONE" — a tag nobody typed. Skip instead of storing it.
            continue
        tag = str(raw).strip().upper()[:TAG_MAX_LENGTH]
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


class TripPostTag(Base):
    """One tag on one trip post — the searchable form of `TripPost.tags`.

    Why a table instead of the JSON column this replaced: tag filtering must be
    exact, unbounded and indexable on **both** SQLite (dev/test) and PostgreSQL
    (production). JSON containment is spelled differently on the two dialects, so
    the previous implementation fell back to scanning at most 1000 rows in memory
    — which silently dropped matches on a larger dataset and reported a `total`
    that was only true inside the scan window. A row-per-tag table is
    dialect-neutral, so dev and prod cannot diverge.
    """

    __tablename__ = "trip_post_tags"
    __table_args__ = (
        # The composite primary key indexes (trip_post_id, tag) — i.e. it can
        # serve "tags of this post", but *not* "posts carrying this tag". The
        # filter query needs the leading column to be `tag`, hence this index.
        Index("ix_trip_post_tags_tag", "tag"),
    )

    trip_post_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trip_posts.id", ondelete="CASCADE"), primary_key=True
    )
    # Primary-key membership gives "a post cannot carry the same tag twice" for
    # free, which is exactly the invariant the normaliser establishes.
    tag: Mapped[str] = mapped_column(String(TAG_MAX_LENGTH), primary_key=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TripPostTag {self.trip_post_id} {self.tag}>"


class TripPost(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An invitation to find travel companions.

    Location granularity is deliberately coarse — country plus city — to honour
    the precise-location privacy rule (Security Spec §2.2).

    `destination_city` is a display string and `city_id` is the canonical
    reference. Both are kept because they answer different questions:
    `destination_city` is what the card renders, and it survives even if a city
    is later removed from the reference table; `city_id` is what makes the city
    *verifiable* — it is the only value a client cannot invent, and it is where
    the map layer gets its coordinates.

    There is no coordinate column here on purpose. Coordinates are joined from
    `cities`, so this table cannot hold a precise location even if a future
    endpoint tried to write one.
    """

    __tablename__ = "trip_posts"

    creator_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )

    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    destination_country: Mapped[str] = mapped_column(String(80), nullable=False)
    destination_city: Mapped[str | None] = mapped_column(String(80), nullable=True)

    #: Canonical city. `SET NULL`, not `CASCADE`: a trip is not *about* the city
    #: row, and re-importing GeoNames must never delete trips. Nullable because
    #: the field is optional, and because `destination_city` may be set to a
    #: legacy free-text value that has no matching row.
    city_id: Mapped[int | None] = mapped_column(
        ForeignKey("cities.id", ondelete="SET NULL"), index=True, nullable=True
    )

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    budget_type: Mapped[BudgetType] = mapped_column(
        enum_col(BudgetType, "budget_type"), default=BudgetType.MODERATE, nullable=False
    )
    target_gender: Mapped[TargetGender] = mapped_column(
        enum_col(TargetGender, "target_gender"), default=TargetGender.ANY, nullable=False
    )

    # e.g. ["PHOTOGRAPHY", "FOOD", "HIKING"] — stored as rows in `trip_post_tags`.
    #
    # `selectin` is load-bearing, not an optimisation. The `tags` property below
    # reads this collection, and `TripPostOut.model_validate(post)` reads `tags`
    # on every serialisation — so a lazy load here would surface as a
    # `MissingGreenlet` (async) or an `InvalidRequestError` under `lazy="raise"`,
    # exactly the trap that made `GET /trips/{id}` answer 500.
    _tag_rows: Mapped[list["TripPostTag"]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        # Tags are returned in a stable (alphabetical) order rather than in
        # whatever order the database happens to hand the rows back.
        order_by="TripPostTag.tag",
    )

    @property
    def tags(self) -> list[str]:
        """Tag list as the API exposes it (plain strings, not ORM rows)."""
        return [row.tag for row in self._tag_rows]

    @tags.setter
    def tags(self, values) -> None:
        # Replacing the whole collection is what makes `delete-orphan` prune the
        # removed tags on update.
        self._tag_rows = [TripPostTag(tag=tag) for tag in normalise_tags(values)]

    looking_for_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[TripStatus] = mapped_column(
        enum_col(TripStatus, "trip_status"), default=TripStatus.OPEN, nullable=False
    )

    creator: Mapped["Profile"] = relationship(
        back_populates="trip_posts", lazy="selectin"
    )
    applications: Mapped[list["TripApplication"]] = relationship(
        back_populates="trip_post", cascade="all, delete-orphan",
        lazy="raise", passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TripPost {self.id} -> {self.destination_city or self.destination_country}>"


class TripApplication(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trip_applications"
    __table_args__ = (
        UniqueConstraint("trip_post_id", "applicant_id", name="uq_trip_applicant"),
    )

    trip_post_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trip_posts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ApplicationStatus] = mapped_column(
        enum_col(ApplicationStatus, "application_status"),
        default=ApplicationStatus.PENDING,
        nullable=False,
    )

    trip_post: Mapped["TripPost"] = relationship(
        back_populates="applications", lazy="raise"
    )
    applicant: Mapped["Profile"] = relationship(lazy="selectin")
