"""Tests for the role-management CLI (`scripts/manage_roles.py`).

An operations tool deserves tests for the same reason application code does — but
these focus on the *guards*, because that is where the damage would be. Granting
admin to the wrong account, or locking everyone out of the admin surface, is not
recoverable by clicking around the UI.
"""
import asyncio
import importlib.util
import uuid
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "manage_roles", Path(__file__).resolve().parents[1] / "scripts" / "manage_roles.py"
)
assert _SPEC and _SPEC.loader
manage_roles = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(manage_roles)


@pytest.fixture(autouse=True)
def reset_roles(client, raw_db):
    """Start every test with zero administrators.

    The test database is shared across the whole session, so an admin promoted by
    another test file would make `demote`'s "last administrator" guard behave
    differently here. Resetting makes the guard's premise deterministic.

    `client` is a dependency for its side effect, not its value: the schema is
    created by the app's lifespan, which only runs when the TestClient context
    manager is entered. Without it this fixture runs before the `users` table
    exists.
    """
    raw_db.execute("UPDATE users SET role = 'USER'")
    raw_db.commit()
    yield


def _role_of(raw_db, profile: dict) -> str:
    row = raw_db.execute(
        "SELECT role FROM users WHERE id = ?",
        (profile["user_id"].replace("-", ""),),
    ).fetchone()
    assert row is not None
    return row[0]


def _admin_count(raw_db) -> int:
    return raw_db.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'ADMIN' AND deleted_at IS NULL"
    ).fetchone()[0]


# --- promote ---------------------------------------------------------------


def test_promote_grants_admin(raw_db, register_user):
    user = register_user()
    assert _role_of(raw_db, user["profile"]) == "USER"

    assert asyncio.run(manage_roles.cmd_promote(user["phone"])) == manage_roles.EXIT_OK
    assert _role_of(raw_db, user["profile"]) == "ADMIN"
    assert _admin_count(raw_db) == 1


def test_promote_is_idempotent(raw_db, register_user):
    user = register_user()
    asyncio.run(manage_roles.cmd_promote(user["phone"]))
    assert asyncio.run(manage_roles.cmd_promote(user["phone"])) == manage_roles.EXIT_OK
    assert _admin_count(raw_db) == 1


def test_promote_unknown_phone_is_not_found(raw_db):
    result = asyncio.run(manage_roles.cmd_promote("+85299999999"))
    assert result == manage_roles.EXIT_NOT_FOUND
    assert _admin_count(raw_db) == 0


def test_promote_refuses_a_deleted_account(raw_db, register_user):
    """An admin that cannot log in is worse than no admin: the surface looks staffed."""
    user = register_user()
    raw_db.execute(
        "UPDATE users SET deleted_at = ? WHERE id = ?",
        ("2026-01-01T00:00:00", user["profile"]["user_id"].replace("-", "")),
    )
    raw_db.commit()

    assert asyncio.run(manage_roles.cmd_promote(user["phone"])) == manage_roles.EXIT_ERROR
    assert _admin_count(raw_db) == 0


# --- demote ----------------------------------------------------------------


def test_demote_refuses_to_remove_the_last_admin(raw_db, register_user):
    """The lockout guard: this is the one mistake that cannot be fixed from the UI."""
    user = register_user()
    asyncio.run(manage_roles.cmd_promote(user["phone"]))

    result = asyncio.run(manage_roles.cmd_demote(user["phone"], force=False))
    assert result == manage_roles.EXIT_ERROR
    assert _role_of(raw_db, user["profile"]) == "ADMIN"


def test_demote_with_force_removes_the_last_admin(raw_db, register_user):
    user = register_user()
    asyncio.run(manage_roles.cmd_promote(user["phone"]))

    assert asyncio.run(manage_roles.cmd_demote(user["phone"], force=True)) == manage_roles.EXIT_OK
    assert _role_of(raw_db, user["profile"]) == "USER"
    assert _admin_count(raw_db) == 0


def test_demote_is_allowed_while_another_admin_remains(raw_db, register_user):
    first = register_user()
    second = register_user()
    asyncio.run(manage_roles.cmd_promote(first["phone"]))
    asyncio.run(manage_roles.cmd_promote(second["phone"]))

    # No --force needed: removing this one leaves the other.
    assert asyncio.run(manage_roles.cmd_demote(first["phone"], force=False)) == manage_roles.EXIT_OK
    assert _admin_count(raw_db) == 1
    assert _role_of(raw_db, second["profile"]) == "ADMIN"


def test_demote_a_non_admin_is_a_no_op(raw_db, register_user):
    user = register_user()
    assert asyncio.run(manage_roles.cmd_demote(user["phone"], force=False)) == manage_roles.EXIT_OK
    assert _role_of(raw_db, user["profile"]) == "USER"


def test_demote_unknown_phone_is_not_found(raw_db):
    assert (
        asyncio.run(manage_roles.cmd_demote("+85299999999", force=True))
        == manage_roles.EXIT_NOT_FOUND
    )


def test_a_deleted_admin_does_not_count_towards_the_last_admin_guard(raw_db, register_user):
    """Otherwise a deleted admin would satisfy the guard and let you remove the only
    usable one — leaving the surface unreachable while the tool reports success."""
    live = register_user()
    gone = register_user()
    asyncio.run(manage_roles.cmd_promote(live["phone"]))
    asyncio.run(manage_roles.cmd_promote(gone["phone"]))
    raw_db.execute(
        "UPDATE users SET deleted_at = ? WHERE id = ?",
        ("2026-01-01T00:00:00", gone["profile"]["user_id"].replace("-", "")),
    )
    raw_db.commit()
    assert _admin_count(raw_db) == 1

    # The deleted admin is not a usable administrator, so this must still refuse.
    assert (
        asyncio.run(manage_roles.cmd_demote(live["phone"], force=False))
        == manage_roles.EXIT_ERROR
    )


# --- list ------------------------------------------------------------------


def test_list_reports_when_there_are_no_admins(raw_db, capsys):
    assert asyncio.run(manage_roles.cmd_list()) == manage_roles.EXIT_OK
    assert "No administrators" in capsys.readouterr().out


def test_list_flags_inactive_admins(raw_db, register_user, capsys):
    user = register_user()
    asyncio.run(manage_roles.cmd_promote(user["phone"]))
    raw_db.execute(
        "UPDATE users SET is_active = 0 WHERE id = ?",
        (user["profile"]["user_id"].replace("-", ""),),
    )
    raw_db.commit()

    asyncio.run(manage_roles.cmd_list())
    out = capsys.readouterr().out
    assert user["phone"] in out
    # An "admin" who cannot log in should be visibly marked as such.
    assert "inactive" in out


# --- the target database is never a secret ---------------------------------


def test_the_printed_target_redacts_credentials():
    from app.core.config import settings

    original = settings.DATABASE_URL
    try:
        settings.DATABASE_URL = "postgresql+asyncpg://tripmate:hunter2@db.internal:5432/tripmate"
        described = manage_roles._describe_target()
    finally:
        settings.DATABASE_URL = original

    assert "hunter2" not in described
    assert "db.internal" in described


def test_the_cli_is_not_reachable_over_http():
    """A role change must require database access, not an authenticated request."""
    from app.main import app

    paths = [getattr(route, "path", "") for route in app.routes]
    assert not any("role" in path and "admin" in path for path in paths), paths
