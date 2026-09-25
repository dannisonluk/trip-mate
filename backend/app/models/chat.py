"""Realtime chat models: rooms, members, messages (spec-aligned)."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.profile import Profile


def direct_pair_key(a: uuid.UUID | str, b: uuid.UUID | str) -> str:
    """Canonical key for a 1:1 room, **order-independent**.

    `DIRECT` rooms were deduplicated by comparing the two member sets in Python,
    which meant the only thing preventing a second room for the same pair was
    that comparison — and nothing prevented two concurrent requests from both
    finding nothing and both inserting. Two people then had two rooms, each
    holding half the conversation, with no way to tell which was real.

    Storing the pair as a single sorted, colon-joined string turns "these two
    people" into a value the database can put a UNIQUE constraint on. Sorting is
    what makes it order-independent: `A:B` and `B:A` are the same relationship,
    and keying on unsorted order would let both spellings coexist.
    """
    return ":".join(sorted([str(a), str(b)]))


class ChatRoom(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chat_rooms"
    __table_args__ = (
        # Only DIRECT rooms have a pair key; the index is partial so the many
        # TRIP rooms (all NULL) do not collide with each other. The same NULL-is-
        # distinct reasoning that makes `uq_review_once_per_untripped_pair`
        # necessary applies in reverse here: without the predicate, every TRIP
        # room would have to share one NULL and the second group room would fail.
        Index(
            "uq_chat_room_direct_pair",
            "direct_pair_key",
            unique=True,
            postgresql_where=text("direct_pair_key IS NOT NULL"),
            sqlite_where=text("direct_pair_key IS NOT NULL"),
        ),
    )

    # "DIRECT" (1:1) | "TRIP" (group tied to a trip post)
    room_type: Mapped[str] = mapped_column(String(10), default="DIRECT", nullable=False)
    trip_post_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trip_posts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)

    #: Canonical `min_id:max_id` for DIRECT rooms; NULL for TRIP rooms. Derived
    #: via `direct_pair_key` — never written from client input.
    direct_pair_key: Mapped[str | None] = mapped_column(String(73), nullable=True)

    members: Mapped[list["ChatRoomMember"]] = relationship(
        back_populates="room", cascade="all, delete-orphan",
        lazy="selectin", passive_deletes=True,
    )
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="room", cascade="all, delete-orphan",
        lazy="raise", passive_deletes=True,
    )


class ChatRoomMember(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "chat_room_members"
    __table_args__ = (UniqueConstraint("room_id", "profile_id", name="uq_room_member"),)

    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_rooms.id", ondelete="CASCADE"), index=True, nullable=False
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    room: Mapped["ChatRoom"] = relationship(back_populates="members", lazy="raise")
    profile: Mapped["Profile"] = relationship(lazy="selectin")


class ChatMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chat_messages"

    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_rooms.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # SET NULL so a deleted account leaves the message thread intact (anonymized).
    sender_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(default=False, nullable=False)

    room: Mapped["ChatRoom"] = relationship(back_populates="messages", lazy="raise")
