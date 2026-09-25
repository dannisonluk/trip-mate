"""Uploads router — server-proxied image upload with EXIF stripping (§4.2)."""
from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from app.core.config import settings
from app.core.deps import CurrentProfile
from app.core.rate_limit import UPLOAD_RATE, limit
from app.services.storage import UploadError, presign_put, store_image

router = APIRouter(prefix="/uploads", tags=["uploads"])


class PresignRequest(BaseModel):
    """No `prefix` field, deliberately.

    The prefix decides where in the bucket the object is written, so accepting it
    from the client made the object namespace caller-controlled. It used to be a
    field with the default `"uploads"`, and nothing validated it: a caller could
    aim the pre-signed PUT at any prefix it liked, including one shaped like
    another user's namespace, because `presign_put` goes straight to S3 and never
    touches the local backend's path-traversal guard.

    The prefix is now derived from the authenticated profile, so the only
    namespace a caller can write to is their own.
    """

    content_type: str


class UploadResult(BaseModel):
    url: str
    content_type: str


class PresignResult(BaseModel):
    object_key: str
    upload_url: str
    expires_in: int


@router.post("/image", response_model=UploadResult, status_code=status.HTTP_201_CREATED)
@limit(UPLOAD_RATE)
async def upload_image(request: Request, profile: CurrentProfile, file: UploadFile = File(...)):
    """Validate magic bytes, strip ALL metadata (incl. EXIF GPS), then store."""
    raw = await file.read()
    if len(raw) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 5 MB)")

    try:
        url = store_image(raw, prefix=f"profiles/{profile.id}")
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return UploadResult(url=url, content_type=file.content_type or "image/*")


@router.post("/presign", response_model=PresignResult)
@limit(UPLOAD_RATE)
async def presign(payload: PresignRequest, request: Request, profile: CurrentProfile):
    """Optional direct-to-storage upload path (S3/R2 only).

    The prefix is the caller's own profile namespace and is not client-supplied
    — see `PresignRequest`.
    """
    try:
        key, url = presign_put(payload.content_type, prefix=f"profiles/{profile.id}")
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PresignResult(
        object_key=key, upload_url=url, expires_in=settings.PRESIGN_EXPIRE_SECONDS
    )
