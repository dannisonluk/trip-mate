"""Object storage helpers (§4.2).

Security contract:
  * Only image/jpeg, image/png, image/webp accepted.
  * Size capped by settings.MAX_UPLOAD_BYTES (default 5 MB).
  * Images are re-encoded server-side, which strips ALL metadata (EXIF GPS,
    device info) before the object is persisted.
  * S3/R2 credentials never reach the browser; uploads are proxied through the
    API (or a pre-signed PUT scoped to a single key + content-type).
"""
from __future__ import annotations

import io
import re
import uuid
from pathlib import Path

from PIL import Image

from app.core.config import settings

ALLOWED_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_PIL_FORMAT = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}


class UploadError(ValueError):
    """Raised for any rejected upload (bad type, too large, corrupt image)."""


def sniff_mime(data: bytes) -> str | None:
    """Detect the real content type from magic bytes (never trust the client)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def sanitize_image(data: bytes) -> tuple[bytes, str, str]:
    """Validate and re-encode an image, stripping all metadata/EXIF.

    Returns (clean_bytes, mime, extension).
    """
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise UploadError(f"File exceeds {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")

    mime = sniff_mime(data)
    if mime is None or mime not in ALLOWED_MIME:
        raise UploadError("Unsupported file type. Allowed: JPEG, PNG, WebP.")

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise UploadError("Corrupt or unreadable image.") from exc

    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    ext = ALLOWED_MIME[mime]
    out = io.BytesIO()
    save_kwargs: dict = {}
    if ext == "jpg":
        save_kwargs = {"quality": 88, "optimize": True}
    # Re-encoding without passing `exif=` drops all EXIF/GPS metadata.
    img.save(out, format=_PIL_FORMAT[ext], **save_kwargs)
    return out.getvalue(), mime, ext


#: Object-key namespaces a caller may write into. Deliberately closed: the
#: prefix decides *where in the bucket* an object lands, so an unvalidated one is
#: a write primitive rather than a path segment.
ALLOWED_PREFIXES = ("uploads", "avatars", "trips")
#: Per-profile namespace, e.g. `profiles/<uuid>`. Matched exactly — the id must
#: be a uuid, so `profiles/../../evil` and `profiles/other-user` both fail.
_PROFILE_PREFIX = re.compile(
    r"^profiles/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def _validate_prefix(prefix: str) -> str:
    """Reject any prefix that could escape or collide with another namespace.

    `build_key` interpolates the prefix straight into the object key, so a value
    like `../../etc` or one with a leading `/` decides the destination. The local
    backend's `_local_path` has a traversal guard, but **presign is S3-only** —
    the guard lives in a function presign never calls, so nothing was checking
    it on that path at all.

    An allow-list rather than a sanitising pass: a filter that strips `..` and
    `/` has to enumerate every way to write a namespace escape, whereas a fixed
    set (plus one exactly-specified pattern) has nothing to enumerate.
    """
    if prefix in ALLOWED_PREFIXES or _PROFILE_PREFIX.match(prefix):
        return prefix
    raise UploadError(
        "Unsupported upload prefix. Allowed: "
        + ", ".join(ALLOWED_PREFIXES)
        + ", profiles/<uuid>."
    )


def build_key(prefix: str, ext: str) -> str:
    return f"{_validate_prefix(prefix)}/{uuid.uuid4().hex}.{ext}"


# --- Local backend ---------------------------------------------------------
def _local_path(key: str) -> Path:
    root = Path(settings.LOCAL_UPLOAD_DIR).resolve()
    path = (root / key).resolve()
    if not str(path).startswith(str(root)):
        raise UploadError("Invalid object key.")  # path traversal guard
    return path


def _put_local(key: str, data: bytes) -> str:
    path = _local_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    base = settings.S3_PUBLIC_BASE_URL or "/media"
    return f"{base}/{key}"


# --- S3 / Cloudflare R2 backend -------------------------------------------
def _s3_client():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL or None,
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
    )


def _put_s3(key: str, data: bytes, mime: str) -> str:
    client = _s3_client()
    client.put_object(
        Bucket=settings.S3_BUCKET,
        Key=key,
        Body=data,
        ContentType=mime,
        CacheControl="public, max-age=31536000, immutable",
    )
    base = settings.S3_PUBLIC_BASE_URL or f"{settings.S3_ENDPOINT_URL}/{settings.S3_BUCKET}"
    return f"{base.rstrip('/')}/{key}"


def store_image(data: bytes, *, prefix: str = "avatars") -> str:
    """Sanitize and persist an image, returning its public URL."""
    clean, mime, ext = sanitize_image(data)
    key = build_key(prefix, ext)
    if settings.STORAGE_BACKEND == "s3":
        return _put_s3(key, clean, mime)
    return _put_local(key, clean)


def presign_put(content_type: str, *, prefix: str = "uploads") -> tuple[str, str]:
    """Return (object_key, presigned_url) for a scoped direct upload.

    Only offered for the S3 backend. The pre-signed PUT is bound to a single
    key and an exact content-type, so it cannot be reused to write elsewhere.
    """
    if settings.STORAGE_BACKEND != "s3":
        raise UploadError("Pre-signed uploads require the S3 storage backend.")
    if content_type not in ALLOWED_MIME:
        raise UploadError("Unsupported content type.")

    key = build_key(prefix, ALLOWED_MIME[content_type])
    client = _s3_client()
    url = client.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=settings.PRESIGN_EXPIRE_SECONDS,
    )
    return key, url


def local_media_dir() -> Path:
    root = Path(settings.LOCAL_UPLOAD_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root
