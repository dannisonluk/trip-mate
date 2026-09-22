"""Profile & travel-history schemas.

Deliberate split:
  * ProfilePublic  — what ANY authenticated user may see (no contact details).
  * ProfilePrivate — what the OWNER sees.
"""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import BudgetType, Gender

MBTI_RE = r"^[EI][SN][TF][JP]$"


class TravelHistoryCreate(BaseModel):
    country: str = Field(min_length=1, max_length=80)
    # Coarse location only — never a street address (§2.2).
    city: str | None = Field(default=None, max_length=80)
    start_date: date | None = None
    end_date: date | None = None
    budget_type: BudgetType = BudgetType.MODERATE
    summary: str | None = Field(default=None, max_length=1000)
    photo_urls: list[str] = Field(default_factory=list, max_length=9)
    is_public: bool = True

    @field_validator("end_date")
    @classmethod
    def _check_dates(cls, v, info):
        start = info.data.get("start_date")
        if v and start and v < start:
            raise ValueError("end_date must not be earlier than start_date")
        return v


class TravelHistoryOut(TravelHistoryCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime


class ProfileUpdate(BaseModel):
    nickname: str | None = Field(default=None, min_length=2, max_length=60)
    avatar_url: str | None = Field(default=None, max_length=512)
    bio: str | None = Field(default=None, max_length=1000)
    mbti: str | None = Field(default=None, max_length=4)
    travel_style_tags: list[str] | None = Field(default=None, max_length=12)
    languages: list[str] | None = Field(default=None, max_length=12)
    gender: Gender | None = None

    @field_validator("mbti")
    @classmethod
    def _check_mbti(cls, v: str | None) -> str | None:
        import re

        if v is None or v == "":
            return None
        upper = v.upper()
        if not re.match(MBTI_RE, upper):
            raise ValueError("MBTI must be 4 letters, e.g. ENFP")
        return upper


class ProfileStats(BaseModel):
    trips_count: int = 0
    reviews_count: int = 0
    average_rating: float | None = None


class ProfilePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nickname: str
    avatar_url: str | None = None
    bio: str | None = None
    mbti: str | None = None
    travel_style_tags: list[str] = []
    languages: list[str] = []
    gender: Gender | None = None
    created_at: datetime
    stats: ProfileStats = Field(default_factory=ProfileStats)


class ProfilePrivate(ProfilePublic):
    user_id: uuid.UUID
    is_verified: bool = False
    updated_at: datetime
