"""Audit-trail tests.

The audit table is the one place in this codebase where the *absence* of a
feature is the feature: nothing may edit or delete an entry, and an entry may
never describe something that did not commit. Those are the properties worth
testing, so most of what follows asserts on the raw table rather than the API —
the API is deliberately too narrow to show what actually landed.

Everything here reads `audit_logs` through the `raw_db` fixture, which is a
separate `sqlite3` connection. That is intentional: it proves the rows are really
in the database, not just in a session the test happens to share.
"""
import asyncio
import json
import uuid

import pytest

_DETAIL_COLUMNS = "action, actor_profile_id, target_type, target_id, detail, request_id, ip_address, user_agent"


def _parse_json(value):
    """Decode the JSON column as raw SQLite gives it back.

    `JSON` serialises a Python `None` to the *text* `null` (SQLAlchemy's
    `none_as_null` defaults to False), so a raw read returns the four-character
    string rather than None. Parsing here means assertions can talk about the
    value that was stored instead of its encoding.
    """
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


@pytest.fixture(autouse=True)
def clean_audit_logs(raw_db):
    """Start every test here from an empty trail.

    The test database is created once per session and only ever `create_all`-ed,
    never dropped, so audit rows written by an earlier test are still present.
    Assertions like "exactly one USER_BLOCKED row" are about *this* test's action,
    and without truncation they quietly count every other test's too.

    The `sqlite_master` check keeps this honest rather than swallowing errors: if
    the table does not exist yet the app's lifespan has not run, and there is
    nothing to clean.
    """
    exists = raw_db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit_logs'"
    ).fetchone()
    if exists:
        raw_db.execute("DELETE FROM audit_logs")
        raw_db.commit()
    yield


def _norm(value):
    """Canonicalise a UUID read back from raw SQLite.

    `Uuid(as_uuid=True)` is stored as 32 hex characters with no dashes, while the
    API returns the dashed form. Normalising here means an assertion can compare
    against the id the API gave the test, instead of hand-stripping dashes at
    every call site and silently comparing the wrong thing if one is missed.
    """
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return value


def _rows(raw_db, action: str | None = None, actor: str | None = None):
    """Read audit rows directly, oldest first, with ids canonicalised."""
    sql = f"SELECT {_DETAIL_COLUMNS} FROM audit_logs"
    clauses, params = [], []
    if action is not None:
        clauses.append("action = ?")
        params.append(action)
    if actor is not None:
        clauses.append("actor_profile_id = ?")
        params.append(str(actor).replace("-", ""))
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    rows = raw_db.execute(sql + " ORDER BY created_at, rowid", params).fetchall()
    return [
        (row[0], _norm(row[1]), row[2], _norm(row[3]), _parse_json(row[4]), *row[5:])
        for row in rows
    ]


def _only(raw_db, action: str):
    """Exactly one row for `action`, or a readable failure."""
    rows = _rows(raw_db, action=action)
    assert len(rows) == 1, f"expected 1 {action} row, got {len(rows)}: {rows}"
    return rows[0]


# --- RBAC ------------------------------------------------------------------


def test_require_role_normalises_case_and_rejects_unknown_roles():
    """The comparison must not depend on casing, and a typo must fail loudly.

    `UserRole.ADMIN` is the string "ADMIN". A call site written
    `require_role("admin")` therefore compares unequal and 403s every caller,
    administrator included — a total, silent lockout of the admin surface. This
    test pins both halves of the fix: casing is irrelevant, and an unknown role
    is rejected when the dependency is built rather than per request.
    """
    from app.core.deps import require_role

    for spelling in ("admin", "ADMIN", "Admin", " admin "):
        assert callable(require_role(spelling))

    with pytest.raises(ValueError, match="unknown role"):
        require_role("admni")


def test_admin_endpoints_are_reachable_only_by_admins(client, register_user, admin_user):
    """Regression: this is the test whose absence hid the casing bug."""
    user = register_user()
    for path in ("/api/v1/admin/reports", "/api/v1/admin/audit-logs"):
        assert client.get(path, headers=user["headers"]).status_code == 403, path

    admin = admin_user()
    for path in ("/api/v1/admin/reports", "/api/v1/admin/audit-logs"):
        assert client.get(path, headers=admin["headers"]).status_code == 200, path


def test_anonymous_callers_get_401_not_403(client):
    assert client.get("/api/v1/admin/audit-logs").status_code == 401


# --- blocking (§3.2) -------------------------------------------------------


def test_block_writes_one_entry_with_actor_target_and_request_id(client, raw_db, register_user):
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")

    resp = client.post(
        f"/api/v1/profiles/{bob['profile']['id']}/block",
        headers={**alice["headers"], "X-Request-ID": "req-block-1"},
    )
    assert resp.status_code == 201, resp.text

    row = _only(raw_db, "USER_BLOCKED")
    _, actor, target_type, target_id, detail, request_id, ip, ua = row
    assert actor == alice["profile"]["id"]
    assert target_type == "profile"
    assert target_id == bob["profile"]["id"]
    # The client supplied the id, so the trail and the access log agree.
    assert request_id == "req-block-1"
    assert detail is None


def test_unblock_records_only_a_real_state_change(client, raw_db, register_user):
    """A no-op unblock must not inflate the trail with an event that never was."""
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    path = f"/api/v1/profiles/{bob['profile']['id']}/block"

    client.post(path, headers=alice["headers"])
    assert client.delete(path, headers=alice["headers"]).status_code == 204
    # Nothing is blocked now, so this removes nothing.
    assert client.delete(path, headers=alice["headers"]).status_code == 204

    assert len(_rows(raw_db, action="USER_BLOCKED")) == 1
    assert len(_rows(raw_db, action="USER_UNBLOCKED")) == 1


def test_reblocking_records_only_a_real_state_change(client, raw_db, register_user):
    """The mirror of the unblock rule, and it was missing.

    `block_profile` is idempotent — a repeat block inserts no row — but the router
    used to record `USER_BLOCKED` unconditionally, so blocking an
    already-blocked profile appended a second audit row for an event that never
    happened. An audit trail that reports non-events is worse than one with gaps.
    """
    alice = register_user(nickname="Alice")
    bob = register_user(nickname="Bob")
    path = f"/api/v1/profiles/{bob['profile']['id']}/block"

    assert client.post(path, headers=alice["headers"]).status_code == 201
    assert client.post(path, headers=alice["headers"]).status_code == 201
    assert client.post(path, headers=alice["headers"]).status_code == 201

    # Three requests, one block, one entry. The block count is scoped to this
    # pair: `blocks` is not truncated between tests (unlike `audit_logs`), so a
    # global COUNT would include every other test's rows.
    assert len(_rows(raw_db, action="USER_BLOCKED")) == 1
    pair = (
        alice["profile"]["id"].replace("-", ""),
        bob["profile"]["id"].replace("-", ""),
    )
    assert (
        raw_db.execute(
            "SELECT COUNT(*) FROM blocks WHERE blocker_profile_id = ? AND blocked_profile_id = ?",
            pair,
        ).fetchone()[0]
        == 1
    )

    # And a real re-block after unblocking is recorded again.
    assert client.delete(path, headers=alice["headers"]).status_code == 204
    assert client.post(path, headers=alice["headers"]).status_code == 201
    assert len(_rows(raw_db, action="USER_BLOCKED")) == 2


# --- reporting (§3.2) ------------------------------------------------------


def test_report_records_the_reason_enum_but_not_the_free_text(
    client, raw_db, register_user
):
    """`reason` is a closed enum; `detail` is user-written prose.

    The audit table outlives an erasure request, so copying the reporter's prose
    into it would leave a permanent copy of text the user asked to have removed.
    """
    reporter = register_user(nickname="Reporter")
    target = register_user(nickname="Target")
    secret = "he threatened me at the hostel, my number is 91234567"

    resp = client.post(
        "/api/v1/reports",
        headers=reporter["headers"],
        json={
            "reported_profile_id": target["profile"]["id"],
            "reason": "harassment",
            "detail": secret,
        },
    )
    assert resp.status_code == 201, resp.text

    row = _only(raw_db, "REPORT_SUBMITTED")
    _, actor, target_type, target_id, detail, *_ = row
    assert actor == reporter["profile"]["id"]
    assert target_type == "profile"
    assert target_id == target["profile"]["id"]
    # The closed enum is kept...
    assert detail == {"reason": "harassment"}
    # ...and the strongest available probe: the prose appears nowhere in the row.
    assert secret not in str(row)
    assert "91234567" not in str(row)


def test_admin_queue_view_is_audited(client, raw_db, admin_user):
    admin = admin_user()
    resp = client.get("/api/v1/admin/reports", headers=admin["headers"])
    assert resp.status_code == 200

    row = _only(raw_db, "ADMIN_QUEUE_VIEWED")
    assert row[1] == admin["profile"]["id"]
    # The entry must agree with what the admin was actually shown. Asserting a
    # literal count would depend on every other test's leftover reports.
    assert row[4]["returned"] == len(resp.json())
    assert row[4]["status_filter"] is None


def test_report_status_change_records_the_transition(client, raw_db, register_user, admin_user):
    """ "Who moved this from open to dismissed" is unrecoverable after the fact."""
    reporter = register_user(nickname="Reporter")
    target = register_user(nickname="Target")
    created = client.post(
        "/api/v1/reports",
        headers=reporter["headers"],
        json={
            "reported_profile_id": target["profile"]["id"],
            "reason": "spam",
            "detail": "spamming every trip",
        },
    ).json()

    admin = admin_user()
    resp = client.patch(
        f"/api/v1/admin/reports/{created['id']}",
        headers=admin["headers"],
        params={"new_status": "dismissed"},
    )
    assert resp.status_code == 200, resp.text

    row = _only(raw_db, "REPORT_STATUS_CHANGED")
    assert row[1] == admin["profile"]["id"]
    assert row[3] == created["id"]
    assert row[4] == {"from": "open", "to": "dismissed"}


# --- erasure (§2.1) --------------------------------------------------------


def test_hard_delete_keeps_the_record_but_drops_the_identity(client, raw_db, register_user):
    """The entry must survive; the link to the erased identity must not.

    This is the only place the audit row is written *before* the delete, so that
    `ON DELETE SET NULL` can fire. A trail that vanished with the user would be
    useless for exactly the abuse cases that matter.
    """
    victim = register_user(nickname="Victim")
    profile_id = victim["profile"]["id"]

    resp = client.request(
        "DELETE",
        "/api/v1/users/me",
        headers=victim["headers"],
        json={"password": victim["password"], "mode": "hard_delete"},
    )
    assert resp.status_code == 204, resp.text

    row = _only(raw_db, "ACCOUNT_DELETED")
    # The row is still here, but the actor link is gone.
    assert row[1] is None
    assert row[2] == "user"
    assert row[4] == {"mode": "hard_delete"}
    assert profile_id not in str(row)


def test_anonymize_keeps_the_actor_resolvable(client, raw_db, register_user):
    user = register_user(nickname="Ghost")
    resp = client.request(
        "DELETE",
        "/api/v1/users/me",
        headers=user["headers"],
        json={"password": user["password"], "mode": "anonymize"},
    )
    assert resp.status_code == 204, resp.text

    row = _only(raw_db, "ACCOUNT_ANONYMIZED")
    # The profile row survives anonymization, so the actor is still named.
    assert row[1] == user["profile"]["id"]
    assert row[4] == {"mode": "anonymize"}


def test_erasure_with_a_wrong_password_writes_nothing(client, raw_db, register_user):
    user = register_user()
    resp = client.request(
        "DELETE",
        "/api/v1/users/me",
        headers=user["headers"],
        json={"password": "not-the-password", "mode": "hard_delete"},
    )
    assert resp.status_code == 401
    assert _rows(raw_db) == []


# --- transaction sharing ---------------------------------------------------


def test_a_rolled_back_action_leaves_no_audit_entry(client):
    """An entry describing something that did not commit is worse than none.

    `audit.record` deliberately does not commit — it shares the caller's
    transaction. Proven here by recording, rolling back, and checking the table
    from a fresh session.
    """
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.models.audit import AuditLog
    from app.models.enums import AuditAction
    from app.services import audit

    async def scenario() -> int:
        engine = create_async_engine(settings.DATABASE_URL)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as db:
                await audit.record(db, action=AuditAction.USER_BLOCKED)
                await db.rollback()
            async with maker() as db:
                return await db.scalar(select(func.count()).select_from(AuditLog)) or 0
        finally:
            await engine.dispose()

    assert asyncio.run(scenario()) == 0


def test_record_commits_with_the_caller(client, raw_db):
    """The mirror image: when the caller commits, the entry is there."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.models.enums import AuditAction
    from app.services import audit

    async def scenario() -> None:
        engine = create_async_engine(settings.DATABASE_URL)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as db:
                await audit.record(db, action=AuditAction.ADMIN_QUEUE_VIEWED)
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
    assert len(_rows(raw_db, action="ADMIN_QUEUE_VIEWED")) == 1


# --- detail sanitisation ---------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["nickname", "email", "phone", "message", "content", "bio", "body", "comment", "summary"],
)
def test_free_text_keys_are_dropped_from_detail(key):
    """A tripwire, not a boundary: it makes `detail={"nickname": ...}` obvious."""
    from app.services.audit import _sanitise_detail

    cleaned = _sanitise_detail({key: "Alice Wong", "count": 3})
    assert cleaned == {"count": 3}


def test_dropping_a_detail_key_is_logged(caplog):
    """The drop must be visible, or it is indistinguishable from never passing it."""
    from app.services.audit import _sanitise_detail

    with caplog.at_level("WARNING"):
        _sanitise_detail({"nickname": "Alice"})
    assert any("nickname" in record.message for record in caplog.records)


def test_closed_enum_reason_survives_sanitisation():
    """Regression: `"reason"` used to be a free-text hint, so it was dropped.

    `ReportCreate._valid_reason` restricts `reason` to five values, so it is not
    prose — it is the field that says what a report was about. The sanitiser
    matched on the key *name*, discarded it, and every REPORT_SUBMITTED entry
    silently lost its only context.
    """
    from app.services.audit import _sanitise_detail

    assert _sanitise_detail({"reason": "harassment"}) == {"reason": "harassment"}


def test_safe_detail_survives_and_non_scalars_are_stringified():
    from app.services.audit import _sanitise_detail

    cleaned = _sanitise_detail(
        {"from": "open", "to": "dismissed", "count": 2, "flag": True, "ids": [1, 2]}
    )
    assert cleaned["from"] == "open"
    assert cleaned["to"] == "dismissed"
    assert cleaned["count"] == 2
    assert cleaned["flag"] is True
    assert cleaned["ids"] == "[1, 2]"


def test_empty_detail_is_stored_as_null():
    from app.services.audit import _sanitise_detail

    assert _sanitise_detail(None) is None
    assert _sanitise_detail({}) is None
    assert _sanitise_detail({"nickname": "x"}) is None


# --- append-only -----------------------------------------------------------


def test_audit_logs_are_read_only_over_http(client, admin_user):
    """There is no route that can edit or delete an entry, for anyone.

    Checked structurally as well as over HTTP: asserting only on a status code
    would pass if someone added `PATCH /admin/audit-logs/{id}` and it happened to
    404 for the id used here.
    """
    from app.main import app

    mutating = [
        (route.path, method)
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/v1/admin/audit-logs")
        for method in getattr(route, "methods", set())
        if method in {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert mutating == [], f"audit trail must not be mutable over HTTP: {mutating}"

    admin = admin_user()
    entry_id = uuid.uuid4()
    for method in ("PATCH", "PUT", "DELETE"):
        resp = client.request(
            method,
            f"/api/v1/admin/audit-logs/{entry_id}",
            headers=admin["headers"],
            json={"action": "ADMIN_QUEUE_VIEWED"},
        )
        # 404 when no route matches the path, 405 if one matches the path but not
        # the method. Either is fine; a 2xx would not be.
        assert resp.status_code in (404, 405), f"{method} returned {resp.status_code}"


def test_the_model_offers_no_mutable_columns():
    """Append-only is structural: the table has no `updated_at` to write to."""
    from app.models.audit import AuditLog

    columns = set(AuditLog.__table__.columns.keys())
    assert "updated_at" not in columns
    assert columns == {
        "id",
        "actor_profile_id",
        "action",
        "target_type",
        "target_id",
        "detail",
        "request_id",
        "ip_address",
        "user_agent",
        "created_at",
    }


# --- the read API ----------------------------------------------------------


def test_ip_and_user_agent_are_stored_but_never_returned(client, raw_db, register_user):
    """Stored for incident investigation; not published to any admin session."""
    alice = register_user()
    bob = register_user()
    client.post(
        f"/api/v1/profiles/{bob['profile']['id']}/block",
        headers=alice["headers"],
    )
    # Stored at the database level...
    assert _rows(raw_db, action="USER_BLOCKED")[0][6] is not None

    # ...but absent from the API payload.
    admin = _promote(raw_db, alice)
    payload = client.get("/api/v1/admin/audit-logs", headers=admin).json()
    serialised = str(payload)
    assert "ip_address" not in serialised
    assert "user_agent" not in serialised


def test_audit_list_is_newest_first_filterable_and_paginated(
    client, raw_db, register_user, admin_user
):
    alice = register_user()
    bob = register_user()
    for _ in range(3):
        client.post(f"/api/v1/profiles/{bob['profile']['id']}/block", headers=alice["headers"])
        client.delete(f"/api/v1/profiles/{bob['profile']['id']}/block", headers=alice["headers"])

    admin = admin_user()
    page = client.get(
        "/api/v1/admin/audit-logs",
        headers=admin["headers"],
        params={"action": "USER_BLOCKED", "limit": 2, "page": 1},
    ).json()

    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert all(item["action"] == "USER_BLOCKED" for item in page["items"])
    # Newest first: the first page starts at the most recent event.
    assert page["items"][0]["created_at"] >= page["items"][1]["created_at"]

    second = client.get(
        "/api/v1/admin/audit-logs",
        headers=admin["headers"],
        params={"action": "USER_BLOCKED", "limit": 2, "page": 2},
    ).json()
    assert len(second["items"]) == 1
    assert {i["id"] for i in page["items"]}.isdisjoint({i["id"] for i in second["items"]})


def test_actor_nickname_is_resolved_server_side(client, raw_db, register_user, admin_user):
    alice = register_user(nickname="Alice Wong")
    bob = register_user(nickname="Bob Chan")
    client.post(f"/api/v1/profiles/{bob['profile']['id']}/block", headers=alice["headers"])

    admin = admin_user()
    page = client.get(
        "/api/v1/admin/audit-logs",
        headers=admin["headers"],
        params={"action": "USER_BLOCKED"},
    ).json()
    assert page["items"][0]["actor_nickname"] == "Alice Wong"


def test_invalid_action_filter_is_rejected(client, admin_user):
    admin = admin_user()
    resp = client.get(
        "/api/v1/admin/audit-logs",
        headers=admin["headers"],
        params={"action": "NOT_AN_ACTION"},
    )
    assert resp.status_code == 422


def _promote(raw_db, account: dict) -> dict:
    """Promote an already-registered account in place, returning its headers."""
    raw_db.execute(
        "UPDATE users SET role = 'ADMIN' WHERE id = ?",
        (account["profile"]["user_id"].replace("-", ""),),
    )
    raw_db.commit()
    return account["headers"]
