"""Authentication & account schemas (phone-based identity per spec)."""
import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import PasswordPolicyError, validate_password_policy

# Hong Kong mobile: +852 followed by 8 digits (5/6/9 prefix conventionally).
HK_PHONE_RE = re.compile(r"^\+852[456789]\d{7}$")


def normalise_phone(raw: str) -> str:
    """Strip spaces/dashes and coerce common HK formats to E.164 (+852XXXXXXXX)."""
    cleaned = re.sub(r"[\s\-()]", "", raw.strip())
    if cleaned.startswith("852") and not cleaned.startswith("+"):
        cleaned = f"+{cleaned}"
    elif cleaned.startswith("+852"):
        pass
    elif re.fullmatch(r"[456789]\d{7}", cleaned):
        cleaned = f"+852{cleaned}"
    return cleaned


class PhoneMixin(BaseModel):
    phone_number: str = Field(min_length=8, max_length=20)

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        normalised = normalise_phone(v)
        if not HK_PHONE_RE.match(normalised):
            raise ValueError("Phone number must be a valid HK mobile, e.g. +85291234567")
        return normalised


class RegisterRequest(PhoneMixin):
    password: str = Field(min_length=8, max_length=128)
    nickname: str = Field(min_length=2, max_length=60)
    # PDPO §2.1 — explicit consent is mandatory at signup.
    consent_privacy: bool
    consent_terms: bool

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        try:
            validate_password_policy(v)
        except PasswordPolicyError as exc:
            raise ValueError(str(exc)) from exc
        return v

    @field_validator("consent_privacy", "consent_terms")
    @classmethod
    def _must_consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("You must accept the Privacy Policy and Terms of Service.")
        return v


class LoginRequest(PhoneMixin):
    password: str


class OTPRequest(PhoneMixin):
    pass


class OTPVerifyRequest(PhoneMixin):
    code: str = Field(min_length=4, max_length=8)


class OTPResponse(BaseModel):
    sent: bool = True
    expires_in: int
    # Only populated when OTP_DEV_ECHO=true and ENV != production.
    dev_code: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        try:
            validate_password_policy(v)
        except PasswordPolicyError as exc:
            raise ValueError(str(exc)) from exc
        return v


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    phone_number: str
    is_verified: bool
    role: str
    is_active: bool
    created_at: datetime


class AccountDeleteRequest(BaseModel):
    """§2.1 — re-authenticate and choose a deletion mode."""

    password: str
    mode: str = "anonymize"

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, v: str) -> str:
        if v not in {"hard_delete", "anonymize"}:
            raise ValueError("mode must be 'hard_delete' or 'anonymize'")
        return v
