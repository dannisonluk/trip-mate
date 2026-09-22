"""Review schemas (post-trip ratings)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.trip import ProfileSummary


class ReviewCreate(BaseModel):
    reviewee_id: uuid.UUID
    trip_post_id: uuid.UUID | None = None
    rating: int = Field(ge=1, le=5)
    tags: list[str] = Field(default_factory=list, max_length=8)
    comment: str | None = Field(default=None, max_length=1000)


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    reviewer_id: uuid.UUID
    reviewee_id: uuid.UUID
    trip_post_id: uuid.UUID | None = None
    rating: int
    tags: list[str] = []
    comment: str | None = None
    created_at: datetime
    reviewer: ProfileSummary | None = None


class ReviewSummary(BaseModel):
    count: int = 0
    average_rating: float | None = None
    # rating (1..5) -> number of reviews
    distribution: dict[str, int] = {}
