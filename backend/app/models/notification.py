"""In-app notifications.

Design notes
------------
* **Two explicit FK targets instead of a generic `(link_type, link_id)` pair.**
  A generic pointer is more extensible but cannot be enforced by the database,
  so it rots into dangling links. `trip_post_id` / `chat_room_id` keep real
  referential integrity and both degrade to NULL via `ON DELETE SET NULL`.

* **`actor_profile_id` is `SET NULL`, never `CASCADE`.** If the person who
  triggered the notification is erased, the *recipient's* notification must
  survive — otherwise a hard-delete by a third party silently rewrites someone
  else's history. The actor simply resolves to "a deactivated user" at read time.

* **`recipient_profile_id` is `CASCADE`.** Deleting the recipient removes their
  own notifications, which is what the right-to-erasure requires.
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import NotificationType, enum_col

if TYPE_CHECKING:
    from app.models.profile import Profile


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # The dominant query is "my unread notifications, newest first".
        Index(
            "ix_notifications_recipient_read_created",
            "recipient_profile_id",
            "read_at",
            "created_at",
        ),
        # Supports the per-room message upsert (see services/notifications.py).
        Index(
            "ix_notifications_message_dedupe",
            "recipient_profile_id",
            "chat_room_id",
        ),
    )

    recipient_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[NotificationType] = mapped_column(
        enum_col(NotificationType, "notification_type"), nullable=False
    )

    # Rendered server-side so the client never has to assemble user-facing text
    # (and so a deleted actor still produces a sensible sentence).
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Who caused it. SET NULL → survives the actor being erased.
    actor_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Deep-link targets. Exactly zero or one is normally set.
    trip_post_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trip_posts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    chat_room_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_rooms.id", ondelete="SET NULL"), nullable=True, index=True
    )

    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Eager-loaded: the list endpoint always renders the actor's nickname, so a
    # lazy load here would raise MissingGreenlet in async context.
    actor: Mapped["Profile | None"] = relationship(
        foreign_keys=[actor_profile_id], lazy="selectin"
    )

    @property
    def is_read(self) -> bool:
        return self.read_at is not None
