"""Happy-path coverage for the write endpoints that had none.

Why this file exists
--------------------
This project has been bitten **three times** by the same failure mode:

    An endpoint was "covered" by a test that only exercised an *early-return*
    branch — "non-owner -> 404", "blocked -> 404", "bad file type -> 400".
    Because the branch returns *before* the mutation, the write code had never
    been executed by any test. The endpoint was fully broken while the suite
    stayed green.

Confirmed instances:
  * `GET /trips/{id}` returned 500 for every viewer (tech debt #9) — the only
    covering test asserted the blocked-viewer 404.
  * `PATCH /trips/applications/{id}` allowed flipping a REJECTED application to
    ACCEPTED (tech debt #20) — the only covering test asserted the non-creator
    404.
  * `POST /uploads/image` — the only covering test asserted a 400 for an SVG.

So the rule this file enforces is deliberately narrow: **each test must assert a
success status AND an observable effect of the write.** Asserting only the status
code is not enough — a handler can return 201 while writing the wrong row, or
while the real serialization path (which is where #9 hid) is never reached.

Endpoints covered here, all previously with zero or rejection-only coverage:
  1. `POST   /profiles/me/upload-url`   — was NONE
  2. `POST   /uploads/presign`           — was NONE
  3. `DELETE /profiles/me/histories/{id}` — was NONE
  4. `POST   /uploads/image` (success)   — was EARLY-RETURN ONLY
  5. `POST   /images` EXIF stripping     — asserted the *effect*, not just 201
"""
import io
from pathlib import Path

import pytest
from PIL import Image


def _png(colour: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), colour).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_with_exif() -> bytes:
    """A JPEG carrying a full EXIF block, so we can prove it gets stripped.

    `sanitize_image` re-encodes via PIL without passing `exif=`, which drops all
    metadata. The security claim in `storage.py`'s docstring is that EXIF
    (including GPS) never survives — but nothing asserted it. A 201 alone cannot
    tell you whether the bytes stored still carry the camera's metadata.

    Only scalar tags are used. A nested GPSInfo IFD pointer needs a well-formed
    sub-IFD to round-trip through PIL, and a malformed one raises inside
    `Exif.tobytes()` rather than producing a test fixture. Scalar tags prove the
    same property: if *any* EXIF survives re-encoding, the block was not dropped.
    """
    img = Image.new("RGB", (8, 8), (10, 120, 200))
    exif = Image.Exif()
    exif[0x0112] = 1  # Orientation
    exif[0x010F] = "TestCam"  # Make
    exif[0x0110] = "ModelX"  # Model
    exif[0x0132] = "2026:09:22 10:00:00"  # DateTime
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


# --- 1. POST /profiles/me/upload-url --------------------------------------

def test_upload_url_is_rejected_on_the_local_backend(client, register_user):
    """The local backend has no pre-signing, and the failure must be a clean 400.

    This endpoint previously had **no test at all**. It is easy to leave broken,
    because its only realistic failure mode is "not configured for S3" — which
    is exactly the test/dev configuration.

    What we are pinning: `presign_put` raises `UploadError`, and the router turns
    that into a **400 with a message**, not a 500. If the router ever stopped
    catching `UploadError`, this becomes a 500 while the status code assertion
    below is the only thing that notices.
    """
    user = register_user(nickname="Presigner")

    resp = client.post(
        "/api/v1/profiles/me/upload-url",
        headers=user["headers"],
        params={"content_type": "image/jpeg"},
    )

    # Local backend -> presigning unavailable -> handled 400, never a 500.
    assert resp.status_code == 400, resp.text
    assert "S3" in resp.json()["detail"], resp.json()


def test_upload_url_requires_authentication(client):
    """Unauthenticated access must not fall through to the handler."""
    resp = client.post("/api/v1/profiles/me/upload-url")
    assert resp.status_code == 401


# --- 2. POST /uploads/presign ---------------------------------------------

def test_presign_refuses_all_types_on_the_local_backend(client, register_user):
    """Both allowed and disallowed types are refused with the *backend* reason.

    This endpoint had **no test at all**, so neither of its two guards was
    pinned. `presign_put` checks the backend before the content-type allow-list:

        if settings.STORAGE_BACKEND != "s3":     # <- runs first
            raise UploadError("Pre-signed uploads require the S3 storage backend.")
        if content_type not in ALLOWED_MIME:
            raise UploadError("Unsupported content type.")

    A consequence worth knowing, and the reason this test is written the way it
    is: under the local backend the backend check **short-circuits**, so the
    content-type allow-list is unreachable through the HTTP surface. It is
    covered directly below instead of being left unverified. The observable
    contract on the local backend is therefore "everything is a 400 for the S3
    reason" — including a type that would be rejected anyway on S3.
    """
    user = register_user(nickname="PresignLocal")

    for content_type in ("image/png", "application/pdf"):
        resp = client.post(
            "/api/v1/uploads/presign",
            headers=user["headers"],
            json={"content_type": content_type, "prefix": "uploads"},
        )
        assert resp.status_code == 400, (content_type, resp.text)
        assert "S3" in resp.json()["detail"], (content_type, resp.json())


def test_presign_content_type_allow_list_rejects_non_images():
    """Pin the allow-list directly, since HTTP cannot reach it locally.

    Calling the service function is the only way to execute this guard in the
    test environment. Without it, deleting the `ALLOWED_MIME` check would leave
    every test green — and on a real S3 deployment that check is the *only*
    thing stopping a pre-signed PUT for `application/pdf` (or anything else the
    caller names) into the upload bucket.

    `generate_presigned_url` signs locally, so it succeeds without reaching AWS
    and without valid credentials. That is convenient: an allowed type returns a
    URL, which is a *positive* signal that the type guard let it through.
    """
    from app.core.config import settings
    from app.services.storage import ALLOWED_MIME, UploadError, presign_put

    # The backend check must pass for the content-type check to be reached.
    original = settings.STORAGE_BACKEND
    settings.STORAGE_BACKEND = "s3"
    try:
        with pytest.raises(UploadError, match="Unsupported content type"):
            presign_put("application/pdf")

        # Every allowed type must get past the type check and produce a URL.
        for allowed in ALLOWED_MIME:
            key, url = presign_put(allowed)
            assert key.endswith(f".{ALLOWED_MIME[allowed]}"), (allowed, key)
            assert url, f"{allowed} produced no URL"
    finally:
        settings.STORAGE_BACKEND = original


def test_presign_key_is_server_generated_and_cannot_escape_its_prefix():
    """The object key must be server-built, not caller-supplied.

    `build_key` composes `{prefix}/{uuid4().hex}.{ext}`. A caller controls only
    `prefix` — never the filename. Pinning this matters because the whole safety
    argument for the pre-signed PUT is "the URL is bound to exactly one key":
    that only holds while the key is generated server-side.
    """
    from app.core.config import settings
    from app.services.storage import presign_put

    original = settings.STORAGE_BACKEND
    settings.STORAGE_BACKEND = "s3"
    try:
        key, _url = presign_put("image/png", prefix="uploads")
        folder, _, filename = key.rpartition("/")
        assert folder == "uploads", key
        stem, _, ext = filename.partition(".")
        assert ext == "png", key
        # 32 hex chars from uuid4().hex — no dots, no slashes, nothing a client
        # could use to name a second object.
        assert len(stem) == 32 and all(c in "0123456789abcdef" for c in stem), key
    finally:
        settings.STORAGE_BACKEND = original


def test_presign_requires_authentication(client):
    resp = client.post(
        "/api/v1/uploads/presign", json={"content_type": "image/png"}
    )
    assert resp.status_code == 401


# --- 3. DELETE /profiles/me/histories/{entry_id} ---------------------------

def test_history_delete_actually_removes_the_row(client, register_user):
    """The only deletion path for a travel-history entry had **no test**.

    Asserting the 204 is not sufficient: a handler that returned 204 and forgot
    to `commit()` would pass that assertion while deleting nothing. So the test
    re-reads the list through the API and requires it to be empty.

    It also pins the ownership rule in the same breath — Bob must not be able to
    delete Alice's entry — because the guard and the delete live in one handler
    and a rewrite could plausibly preserve one while breaking the other.
    """
    alice = register_user(nickname="HistoryOwner")
    bob = register_user(nickname="HistoryStranger")

    created = client.post(
        "/api/v1/profiles/me/histories",
        headers=alice["headers"],
        json={"country": "Japan", "city": "Osaka", "summary": "autumn colours"},
    )
    assert created.status_code == 201, created.text
    entry_id = created.json()["id"]

    # Confirm it is really there first, otherwise the assertions below could pass
    # against an entry that was never created.
    before = client.get(
        f"/api/v1/profiles/{alice['profile']['id']}/histories",
        headers=alice["headers"],
    )
    assert [h["id"] for h in before.json()] == [entry_id], before.json()

    # A stranger's delete must not succeed.
    assert (
        client.delete(
            f"/api/v1/profiles/me/histories/{entry_id}", headers=bob["headers"]
        ).status_code
        == 404
    )

    deleted = client.delete(
        f"/api/v1/profiles/me/histories/{entry_id}", headers=alice["headers"]
    )
    assert deleted.status_code == 204

    after = client.get(
        f"/api/v1/profiles/{alice['profile']['id']}/histories",
        headers=alice["headers"],
    )
    assert after.status_code == 200
    assert after.json() == [], f"row survived the delete: {after.json()}"


def test_history_delete_is_not_silently_idempotent(client, register_user):
    """Deleting twice must 404 the second time, not 204.

    A 204 on a missing row would mean the handler is not distinguishing "deleted
    something" from "found nothing" — which would also hide a broken delete that
    always reports success.
    """
    user = register_user(nickname="HistoryTwice")
    created = client.post(
        "/api/v1/profiles/me/histories",
        headers=user["headers"],
        json={"country": "Nepal", "city": "Pokhara"},
    )
    entry_id = created.json()["id"]

    assert (
        client.delete(
            f"/api/v1/profiles/me/histories/{entry_id}", headers=user["headers"]
        ).status_code
        == 204
    )
    second = client.delete(
        f"/api/v1/profiles/me/histories/{entry_id}", headers=user["headers"]
    )
    assert second.status_code == 404, second.text


# --- 4. POST /uploads/image — the success path -----------------------------

def test_upload_image_succeeds_and_returns_a_reachable_url(client, register_user):
    """The **success** path of the image upload had never been executed.

    The only pre-existing test (`test_upload_rejects_non_image`) sends an SVG and
    asserts 400. That exercises `sniff_mime` and nothing else — `store_image`,
    `sanitize_image`'s re-encode, `build_key`, and `_put_local` all sat untested.
    A crash anywhere in that chain would have kept the suite green.
    """
    user = register_user(nickname="ImageUploader")

    resp = client.post(
        "/api/v1/uploads/image",
        headers=user["headers"],
        files={"file": ("avatar.png", _png(), "image/png")},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["content_type"] == "image/png"
    # The stored key is server-generated (`{prefix}/{uuid4}.png`), so the URL
    # must contain the caller's own prefix — proving the prefix came from the
    # server and not from anything the client sent.
    assert f"/profiles/{user['profile']['id']}/" in body["url"], body
    assert body["url"].endswith(".png"), body


def test_uploaded_image_has_its_exif_stripped(client, register_user):
    """The security claim is about bytes, so assert on bytes.

    `storage.py` states that re-encoding strips ALL metadata including EXIF GPS.
    Nothing verified it. A 201 cannot: the stored file could still carry the
    camera's metadata.

    Asserting "no EXIF" alone would also pass if the file were truncated to
    nothing, so the decode check is not optional — we confirm the object is
    still a readable image *and* carries no metadata.
    """
    user = register_user(nickname="ExifUploader")
    raw = _jpeg_with_exif()

    # Sanity: the fixture really does carry metadata. Without this the test
    # would pass even if `_jpeg_with_exif()` produced a metadata-free file.
    src = Image.open(io.BytesIO(raw))
    assert dict(src.getexif()), "fixture did not embed any EXIF"
    assert src.getexif().get(0x0112) == 1, "fixture lost Orientation"

    resp = client.post(
        "/api/v1/uploads/image",
        headers=user["headers"],
        files={"file": ("holiday.jpg", raw, "image/jpeg")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Locate the stored object. The URL is `{base}/{prefix}/{uuid}.jpg`; the
    # fixture sets LOCAL_UPLOAD_DIR under a per-run temp dir, so search there
    # rather than assuming the runner's cwd.
    object_name = body["url"].rsplit("/", 1)[-1]
    from app.core.config import settings

    root = Path(settings.LOCAL_UPLOAD_DIR)
    if not root.is_absolute():
        # conftest sets an absolute path, but be explicit if that ever changes.
        root = Path.cwd() / root
    matches = list(root.rglob(object_name))
    assert matches, f"stored object {object_name} not found under {root}"

    out = Image.open(matches[0])
    out.load()  # a truncated or non-image file raises here
    stored_exif = out.getexif()
    assert 0x0112 not in stored_exif, "EXIF Orientation survived re-encode"
    assert not dict(stored_exif), (
        f"EXIF survived re-encoding: {dict(stored_exif)}"
    )


def test_upload_rejects_a_file_over_the_size_cap(client, register_user):
    """The 413 branch must be reachable and must be a 413, not a 400.

    `upload_image` checks the size itself (413) *before* `sanitize_image` also
    checks it (which raises `UploadError` -> 400). Only one of those two can
    ever fire, and which one it is determines the status code a client sees.
    """
    user = register_user(nickname="BigUploader")
    from app.core.config import settings

    oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (settings.MAX_UPLOAD_BYTES + 1)

    resp = client.post(
        "/api/v1/uploads/image",
        headers=user["headers"],
        files={"file": ("huge.png", oversized, "image/png")},
    )
    assert resp.status_code == 413, resp.text
    assert "too large" in resp.json()["detail"].lower(), resp.json()
