"""Chat (room / member / message) schemas."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.trip import ProfileSummary


class RoomCreate(BaseModel):
    """`DIRECT` needs other_profile_id; `TRIP` needs trip_post_id."""

    room_type: str = "DIRECT"
    other_profile_id: uuid.UUID | None = None
    trip_post_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=120)


class RoomMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    profile_id: uuid.UUID
    joined_at: datetime | None = None
    profile: ProfileSummary | None = None


class RoomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    room_type: str
    trip_post_id: uuid.UUID | None = None
    title: str | None = None
    created_at: datetime
    members: list[RoomMemberOut] = []


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    room_id: uuid.UUID
    sender_id: uuid.UUID | None = None
    content: str
    is_deleted: bool
    created_at: datetime


class PaginatedMessages(BaseModel):
    items: list[MessageOut]
    total: int
    page: int
    page_size: int
