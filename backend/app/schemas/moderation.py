"""Moderation schemas: block + report."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    blocker_profile_id: uuid.UUID
    blocked_profile_id: uuid.UUID
    created_at: datetime


class ReportCreate(BaseModel):
    reported_profile_id: uuid.UUID
    reason: str
    detail: str | None = Field(default=None, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _valid_reason(cls, v: str) -> str:
        allowed = {"harassment", "spam", "scam", "inappropriate", "other"}
        if v not in allowed:
            raise ValueError(f"reason must be one of {sorted(allowed)}")
        return v


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    reporter_profile_id: uuid.UUID
    reported_profile_id: uuid.UUID
    reason: str
    detail: str | None = None
    status: str
    created_at: datetime
