"""Trip post, application, and recommendation schemas."""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import ApplicationStatus, BudgetType, Gender, TargetGender, TripStatus
from app.models.trip import TAG_LIMIT, normalise_tags


class ProfileSummary(BaseModel):
    """Lightweight profile card embedded in trip/application payloads."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    nickname: str
    avatar_url: str | None = None
    mbti: str | None = None
    gender: Gender | None = None


class TripPostBase(BaseModel):
    title: str = Field(min_length=4, max_length=120)
    description: str = Field(min_length=10, max_length=3000)
    destination_country: str = Field(min_length=1, max_length=80)
    destination_city: str | None = Field(default=None, max_length=80)
    #: Reference-table id for `destination_city`. `destination_city` is the
    #: display echo; this is the value the client cannot invent. Validated
    #: against `cities` in the router, because storing an id that does not
    #: resolve produces a trip whose location silently never matches.
    city_id: int | None = Field(default=None, ge=1)
    start_date: date | None = None
    end_date: date | None = None
    budget_type: BudgetType = BudgetType.MODERATE
    target_gender: TargetGender = TargetGender.ANY
    tags: list[str] = Field(default_factory=list, max_length=TAG_LIMIT)
    looking_for_count: int = Field(default=1, ge=1, le=20)

    @field_validator("tags")
    @classmethod
    def _canonical_tags(cls, value: list[str]) -> list[str]:
        """Normalise at the boundary so every write path agrees on the stored form.

        The ORM setter normalises too; doing it here as well means the *response*
        echoes the canonical tags, so a client that sends "hiking" immediately
        sees "HIKING" rather than having to discover the rule later.
        """
        return normalise_tags(value)

    @model_validator(mode="after")
    def _check_dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not be earlier than start_date")
        return self


class TripPostCreate(TripPostBase):
    pass


class TripPostUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=4, max_length=120)
    description: str | None = Field(default=None, min_length=10, max_length=3000)
    destination_country: str | None = Field(default=None, max_length=80)
    destination_city: str | None = Field(default=None, max_length=80)
    city_id: int | None = Field(default=None, ge=1)
    start_date: date | None = None
    end_date: date | None = None
    budget_type: BudgetType | None = None
    target_gender: TargetGender | None = None
    tags: list[str] | None = Field(default=None, max_length=TAG_LIMIT)
    looking_for_count: int | None = Field(default=None, ge=1, le=20)
    status: TripStatus | None = None

    @field_validator("tags")
    @classmethod
    def _canonical_tags(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalise_tags(value)

    @model_validator(mode="after")
    def _reject_explicit_nulls(self):
        """`null` is not the same request as "leave this field alone".

        Every field is optional so the patch can be partial, which makes
        `None` double as "absent". For a column that is NOT NULL in the
        database, an explicit `null` therefore reaches
        `setattr(post, field, None)` in `PATCH /trips/{id}` and turns into an
        `IntegrityError` — a 500 for a malformed request, when the right answer
        is 422. `exclude_unset=True` in the router handles the absent case, so
        anyone sending an explicit null meant to clear the field, and these
        fields have nothing to clear to.
        """
        nullable = {"destination_city", "start_date", "end_date", "city_id"}
        offenders = [
            name
            for name in self.model_fields_set - nullable
            if getattr(self, name) is None
        ]
        if offenders:
            raise ValueError(
                "these fields cannot be null: " + ", ".join(sorted(offenders))
            )
        return self


class TripPostOut(TripPostBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    creator_id: uuid.UUID
    creator: ProfileSummary | None = None
    status: TripStatus
    created_at: datetime


class TripApplicationCreate(BaseModel):
    message: str | None = Field(default=None, max_length=1000)


class TripApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    trip_post_id: uuid.UUID
    applicant_id: uuid.UUID
    message: str | None = None
    status: ApplicationStatus
    created_at: datetime
    applicant: ProfileSummary | None = None


class TripPostDetail(TripPostOut):
    applications: list[TripApplicationOut] = []


class RecommendationOut(BaseModel):
    post: TripPostOut
    score: float
    reasons: list[str] = []


class PaginatedTrips(BaseModel):
    items: list[TripPostOut]
    total: int
    page: int
    limit: int
