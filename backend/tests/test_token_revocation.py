"""Refresh-token revocation: logout, rotation, and reuse detection.

These cover the gap called out in docs/SECURITY.md — a stateless refresh JWT
stays valid until it expires unless the server keeps revocation state.
"""
from __future__ import annotations

COOKIE = "tripmate_refresh"
COOKIE_PATH = "/api/v1/auth"


def _cookie(client) -> str | None:
    return client.cookies.get(COOKIE)


def _replay(client, token: str) -> None:
    """Force the client to present exactly one specific refresh cookie value.

    The jar is cleared first: a manually injected cookie has no domain, so it
    would otherwise coexist with a server-issued one and both would be sent.
    """
    client.cookies.clear()
    client.cookies.set(COOKIE, token, path=COOKIE_PATH)


def test_logout_revokes_the_refresh_token(client, register_user):
    register_user()
    assert client.post("/api/v1/auth/refresh").status_code == 200

    assert client.post("/api/v1/auth/logout").status_code == 204
    # The cookie is cleared, so the next refresh cannot authenticate.
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_logout_revokes_even_if_the_cookie_is_replayed(client, register_user):
    """Revocation must live server-side, not just in the cookie jar."""
    register_user()
    stolen = _cookie(client)
    assert stolen

    assert client.post("/api/v1/auth/logout").status_code == 204

    # An attacker holding a copy of the cookie still gets nowhere.
    _replay(client, stolen)
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_refresh_rotates_the_token(client, register_user):
    register_user()
    first = _cookie(client)

    assert client.post("/api/v1/auth/refresh").status_code == 200
    second = _cookie(client)

    assert second, "rotation must issue a new refresh cookie"
    assert second != first, "the refresh token must change on every rotation"


def test_rotated_token_cannot_be_reused(client, register_user):
    register_user()
    first = _cookie(client)
    client.post("/api/v1/auth/refresh")  # rotates first -> second

    _replay(client, first)
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_reuse_detection_invalidates_every_outstanding_token(client, register_user):
    """Replaying a rotated token must burn the whole family, not just that token.

    After a replay we cannot tell the thief from the victim, so the safe move is
    to force both to log in again.
    """
    register_user()
    first = _cookie(client)
    client.post("/api/v1/auth/refresh")
    second = _cookie(client)
    assert second and second != first

    # Attacker replays the already-rotated token.
    _replay(client, first)
    assert client.post("/api/v1/auth/refresh").status_code == 401

    # The legitimate holder's newer token is now dead too (epoch bumped).
    _replay(client, second)
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_password_change_invalidates_refresh_tokens(client, register_user):
    user = register_user()
    assert client.post("/api/v1/auth/refresh").status_code == 200

    resp = client.post(
        "/api/v1/auth/password",
        headers=user["headers"],
        json={"current_password": "Passw0rd123", "new_password": "BrandNew123"},
    )
    assert resp.status_code == 204, resp.text

    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_fresh_login_after_reuse_detection_works(client, register_user):
    """Bumping the epoch must not lock the real user out permanently."""
    user = register_user()
    first = _cookie(client)
    client.post("/api/v1/auth/refresh")

    _replay(client, first)
    assert client.post("/api/v1/auth/refresh").status_code == 401

    # Logging in again issues a token bound to the new epoch.
    client.cookies.clear()  # a real login starts from a clean jar
    resp = client.post(
        "/api/v1/auth/login",
        json={"phone_number": user["phone"], "password": "Passw0rd123"},
    )
    assert resp.status_code == 200, resp.text
    assert client.post("/api/v1/auth/refresh").status_code == 200


def test_refresh_rejects_an_access_token(client, register_user):
    """Token-type confusion must not let an access token mint a new session."""
    user = register_user()
    access = user["headers"]["Authorization"].removeprefix("Bearer ")
    _replay(client, access)
    assert client.post("/api/v1/auth/refresh").status_code == 401
