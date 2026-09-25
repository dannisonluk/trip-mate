"""Notification schemas."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import NotificationType
from app.schemas.trip import ProfileSummary


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: NotificationType
    #: Language-neutral key the client resolves through its dictionary. The
    #: server deliberately does **not** send a sentence: the row outlives the
    #: locale it was written in. See `docs/AUDIT-2026-09-26.md` (B6).
    code: str
    #: Values to interpolate into the client-side template.
    params: dict | None = None
    #: Preview of the triggering content — already user text, shown verbatim.
    body: str | None = None
    read_at: datetime | None = None
    created_at: datetime

    actor: ProfileSummary | None = None
    trip_post_id: uuid.UUID | None = None
    chat_room_id: uuid.UUID | None = None


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    total: int
    unread: int
    page: int
    page_size: int


class UnreadCount(BaseModel):
    unread: int
