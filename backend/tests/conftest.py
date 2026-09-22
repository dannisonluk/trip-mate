"""Pytest fixtures. Environment is configured BEFORE the app is imported."""
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path

# --- Configure the environment before any app import ------------------------
_TMP = Path(tempfile.mkdtemp(prefix="tripmate-test-"))
os.environ.update(
    {
        "ENV": "development",
        "SECRET_KEY": "test-secret-key-not-for-production",
        "DATABASE_URL": f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}",
        "RATE_LIMIT_ENABLED": "false",
        "STORAGE_BACKEND": "local",
        "LOCAL_UPLOAD_DIR": str(_TMP / "uploads"),
        "BACKEND_CORS_ORIGINS": "http://localhost:3000",
        "OTP_DEV_ECHO": "true",
        # The access-log middleware emits one INFO line per request; a few hundred
        # tests would bury the assertions. Warnings and errors still surface.
        "LOG_LEVEL": "WARNING",
        "LOG_FORMAT": "text",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

_counter = {"n": 0}

_DB_URL_PREFIX = "sqlite+aiosqlite:///"


def test_db_path() -> str:
    """Filesystem path of the shared test database."""
    url = os.environ["DATABASE_URL"]
    assert url.startswith(_DB_URL_PREFIX), f"unexpected test DATABASE_URL: {url}"
    return url[len(_DB_URL_PREFIX):]


@pytest.fixture()
def client():
    # Entering the context manager runs the lifespan, which creates the schema
    # (SQLite only) inside the TestClient's own event loop.
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def raw_db():
    """Direct sqlite3 access, for assertions the API deliberately does not expose.

    `PRAGMA foreign_keys=ON` mirrors what the app engine now does on connect, so a
    test can *prove* an ON DELETE CASCADE happened instead of assuming it — the
    whole point of enabling the pragma in `app/db/session.py`.
    """
    con = sqlite3.connect(test_db_path())
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
    finally:
        con.close()


def unique_phone() -> str:
    """Generate a valid, unique HK mobile number for each test user.

    Format: +852 9XXXXXXX (8 digits after the country code).
    """
    _counter["n"] += 1
    suffix = f"{_counter['n']:03d}{uuid.uuid4().int % 10000:04d}"  # 7 digits
    return f"+8529{suffix}"


@pytest.fixture()
def register_user(client):
    """Factory returning {headers, phone, password, profile} for a fresh user."""

    def _make(password: str = "Passw0rd123", nickname: str = "Tester"):
        phone = unique_phone()
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "phone_number": phone,
                "password": password,
                "nickname": nickname,
                "consent_privacy": True,
                "consent_terms": True,
            },
        )
        assert resp.status_code == 201, resp.text
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        profile = client.get("/api/v1/profiles/me", headers=headers).json()
        return {
            "headers": headers,
            "phone": phone,
            "password": password,
            "profile": profile,
        }

    return _make


@pytest.fixture()
def admin_user(client, raw_db, register_user):
    """Factory returning the same shape as `register_user`, but role=ADMIN.

    There is deliberately no API that grants a role, so the promotion is a direct
    write to the row. Note that this fixture is the *only* way to exercise
    `require_role` at all: with no admin test in the suite, a casing bug in that
    dependency (`require_role("admin")` vs the enum value `"ADMIN"`) denied every
    caller — including real administrators — and nothing failed. The fixture
    exists so that class of bug has somewhere to surface.
    """

    def _make(password: str = "Passw0rd123", nickname: str = "Admin"):
        account = register_user(password=password, nickname=nickname)
        # SQLite stores `Uuid` as 32 hex characters, so the WHERE clause must use
        # the undashed form — a dashed id simply matches nothing, and the fixture
        # would promote no one while looking like it had.
        user_id = account["profile"]["user_id"].replace("-", "")
        raw_db.execute("UPDATE users SET role = 'ADMIN' WHERE id = ?", (user_id,))
        raw_db.commit()
        # Read the role back through the API so a test can never assert against a
        # promotion that did not actually land.
        me = client.get("/api/v1/auth/me", headers=account["headers"])
        assert me.status_code == 200, me.text
        assert me.json()["role"] == "ADMIN", me.json()
        return account

    return _make
