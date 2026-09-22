"""Realtime chat models: rooms, members, messages (spec-aligned)."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.profile import Profile


class ChatRoom(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chat_rooms"

    # "DIRECT" (1:1) | "TRIP" (group tied to a trip post)
    room_type: Mapped[str] = mapped_column(String(10), default="DIRECT", nullable=False)
    trip_post_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trip_posts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)

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
