"""Auth router — register / login / refresh / logout / password / OTP (§1.1, §1.2)."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Cookie, HTTPException, Request, Response, status
from jose import JWTError
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.rate_limit import LOGIN_RATE, OTP_RATE, REFRESH_RATE, REGISTER_RATE, WRITE_RATE, limit
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password_async,
    needs_rehash,
    token_remaining_seconds,
    verify_password_async,
    verify_password_dummy_async,
)
from app.models.profile import Profile
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    OTPRequest,
    OTPResponse,
    OTPVerifyRequest,
    PasswordChangeRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.services import otp as otp_service
from app.services import sms
from app.services import token_store

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "tripmate_refresh"
# Generic message: never reveal whether an account exists (§1.2 anti-enumeration).
_GENERIC_AUTH_ERROR = "Invalid credentials"
_INVALID_REFRESH = "Invalid refresh token"


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        httponly=True,                       # unreadable by JS → XSS-safe
        secure=settings.COOKIE_SECURE,       # HTTPS-only in production
        samesite=settings.COOKIE_SAMESITE,   # strict → CSRF-safe
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
        # None (the default, from an empty COOKIE_DOMAIN) omits the Domain
        # attribute entirely → host-only cookie, not sent to sibling subdomains.
        domain=settings.COOKIE_DOMAIN or None,
        path=f"{settings.API_V1_PREFIX}/auth",
    )


async def _issue_tokens(response: Response, user: User, profile: Profile | None) -> TokenResponse:
    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    access = create_access_token(
        user_id=str(user.id),
        role=role,
        profile_id=str(profile.id) if profile else None,
    )
    # Bind the refresh token to the user's current epoch so a later bump
    # invalidates it (see services/token_store).
    epoch = await token_store.current_epoch(str(user.id))
    refresh = create_refresh_token(user_id=str(user.id), epoch=epoch)
    _set_refresh_cookie(response, refresh)
    return TokenResponse(
        access_token=access,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limit(REGISTER_RATE)
async def register(payload: RegisterRequest, request: Request, response: Response, db: DbSession):
    existing = (
        await db.execute(select(User).where(User.phone_number == payload.phone_number))
    ).scalar_one_or_none()
    if existing:
        # Generic message family — avoids account enumeration.
        raise HTTPException(
            status_code=400, detail="Registration failed. Please try different details."
        )

    user = User(
        phone_number=payload.phone_number,
        password_hash=await hash_password_async(payload.password),
        consent_privacy=True,
        consent_terms=True,
        consent_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.flush()

    profile = Profile(
        user_id=user.id,
        nickname=payload.nickname,
        travel_style_tags=[],
        languages=[],
    )
    db.add(profile)
    await db.commit()
    await db.refresh(user)
    await db.refresh(profile)

    return await _issue_tokens(response, user, profile)


@router.post("/login", response_model=TokenResponse)
@limit(LOGIN_RATE)
async def login(payload: LoginRequest, request: Request, response: Response, db: DbSession):
    user = (
        await db.execute(select(User).where(User.phone_number == payload.phone_number))
    ).scalar_one_or_none()
    if user is None or user.is_deleted:
        # Deliberately burn one Argon2 verification before failing. Short-circuit
        # evaluation here would make "no such account" measurably faster than
        # "wrong password", leaking which phone numbers are registered (§1.2).
        await verify_password_dummy_async(payload.password)
        raise HTTPException(status_code=401, detail=_GENERIC_AUTH_ERROR)
    if not await verify_password_async(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail=_GENERIC_AUTH_ERROR)
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    if needs_rehash(user.password_hash):
        user.password_hash = await hash_password_async(payload.password)
        await db.commit()

    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user.id))
    ).scalar_one_or_none()
    return await _issue_tokens(response, user, profile)


@router.post("/refresh", response_model=TokenResponse)
@limit(REFRESH_RATE)
async def refresh(
    request: Request,
    response: Response,
    db: DbSession,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
):
    """Rotate the refresh token.

    Every successful call revokes the presented token and issues a new one. A
    *revoked* token being presented again means it was already used — either it
    was stolen, or the legitimate holder replayed an old copy. Both cases are
    treated as compromise: the user's whole epoch is bumped, forcing a fresh
    login rather than guessing which holder is genuine.
    """
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
        user_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail=_INVALID_REFRESH) from None

    jti = payload.get("jti")

    # --- Reuse detection ---------------------------------------------------
    if jti and await token_store.is_revoked(jti):
        await token_store.bump_epoch(str(user_id))
        raise HTTPException(status_code=401, detail=_INVALID_REFRESH)

    # --- Epoch check (covers bulk invalidation) ----------------------------
    if payload.get("epoch", 0) != await token_store.current_epoch(str(user_id)):
        raise HTTPException(status_code=401, detail=_INVALID_REFRESH)

    user = await db.get(User, user_id)
    if user is None or not user.is_active or user.is_deleted:
        raise HTTPException(status_code=401, detail=_INVALID_REFRESH)

    # --- Rotate ------------------------------------------------------------
    if jti:
        await token_store.revoke(jti, token_remaining_seconds(payload))

    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user.id))
    ).scalar_one_or_none()
    return await _issue_tokens(response, user, profile)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limit(WRITE_RATE)
async def logout(
    request: Request,
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
):
    """Revoke the presented refresh token, then clear the cookie.

    Revocation is what makes logout immediate — without it the cookie would
    remain usable until its natural expiry.
    """
    if refresh_token:
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
            jti = payload.get("jti")
            if jti:
                await token_store.revoke(jti, token_remaining_seconds(payload))
        except JWTError:
            pass  # already invalid or expired — nothing to revoke
    response.delete_cookie(key=REFRESH_COOKIE, path=f"{settings.API_V1_PREFIX}/auth")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
@limit(WRITE_RATE)
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    user: CurrentUser,
    db: DbSession,
):
    if not await verify_password_async(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail=_GENERIC_AUTH_ERROR)
    user.password_hash = await hash_password_async(payload.new_password)
    await db.commit()
    # A password change should not leave older sessions alive.
    await token_store.bump_epoch(str(user.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- SMS OTP (§1.2 / §3.2: max 5 calls per minute) -------------------------
@router.post("/otp/request", response_model=OTPResponse)
@limit(OTP_RATE)
async def request_otp(payload: OTPRequest, request: Request):
    code = await otp_service.issue_otp(payload.phone_number)
    delivered = await sms.send_verification_code(payload.phone_number, code)
    # The code is NEVER returned in production, whatever OTP_DEV_ECHO says.
    echo = code if (settings.OTP_DEV_ECHO and not settings.is_production) else None
    return OTPResponse(
        sent=delivered, expires_in=settings.OTP_TTL_SECONDS, dev_code=echo
    )


@router.post("/otp/verify", status_code=status.HTTP_204_NO_CONTENT)
@limit(OTP_RATE)
async def verify_otp(payload: OTPVerifyRequest, request: Request, db: DbSession):
    user = (
        await db.execute(select(User).where(User.phone_number == payload.phone_number))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=400, detail="Verification failed")

    if not await otp_service.verify_otp(payload.phone_number, payload.code):
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    user.is_verified = True
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return UserOut.model_validate(user)
