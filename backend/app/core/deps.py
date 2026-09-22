"""FastAPI dependencies: authentication, current user/profile, RBAC (§1.3)."""
import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.profile import Profile
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> User:
    if credentials is None or not credentials.credentials:
        raise _CREDENTIALS_EXC
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
        user_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise _CREDENTIALS_EXC from None

    user = await db.get(User, user_id)
    if user is None or not user.is_active or user.is_deleted:
        raise _CREDENTIALS_EXC
    return user


async def get_current_profile(
    user: Annotated[User, Depends(get_current_user)],
    db: DbSession,
) -> Profile:
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user.id))
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


def require_role(*roles: str):
    """Dependency factory enforcing role membership (§1.3 RBAC).

    Two details here are load-bearing, and both were learned the hard way:

    * **The comparison is case-insensitive.** `UserRole.ADMIN` is the string
      `"ADMIN"`, so a call site written as `require_role("admin")` compares
      unequal and denies *every* caller — including genuine administrators —
      while reading as obviously correct. The endpoint returns 403 rather than
      500, so nothing looks broken; the admin surface is simply unreachable.
      Normalising both sides means casing can never decide access.

    * **An unknown role name fails when the dependency is built, not per
      request.** `require_role("admni")` is the same silent, total lockout. The
      check runs at import time, so a typo crashes the app on startup with a
      message that names the bad value, instead of locking out administrators
      quietly for however long it takes someone to notice.
    """
    wanted = {role.strip().upper() for role in roles}
    known = {member.value for member in UserRole}
    unknown = wanted - known
    if unknown:
        raise ValueError(
            f"unknown role(s) {sorted(unknown)}; expected one of {sorted(known)}"
        )

    async def _checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        current = str(getattr(user.role, "value", user.role)).upper()
        if current not in wanted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        return user

    return _checker


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentProfile = Annotated[Profile, Depends(get_current_profile)]
