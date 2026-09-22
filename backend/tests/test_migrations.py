"""Guards against drift between the ORM models and the Alembic migrations.

`alembic check` catches model→migration drift, but only when the migration
history is reachable. This test additionally proves the stronger property the
project actually relies on: **`alembic upgrade head` produces exactly the same
schema as `Base.metadata.create_all`** — so dev (create_all) and production
(Alembic) cannot silently diverge.

Comparison is structural (SQLite PRAGMA introspection), not textual, because
the generated DDL differs only in the *order* of FOREIGN KEY / UNIQUE clauses.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.db.base import Base
import app.models  # noqa: F401 - populate Base.metadata

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_BIN = Path(sys.executable).parent / ("alembic.exe" if os.name == "nt" else "alembic")
IGNORED_TABLES = {"alembic_version"}


def _fingerprint(db_path: str) -> dict:
    """Structural fingerprint: columns, FKs, unique constraints, indexes."""
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [n for (n,) in cur.fetchall() if n not in IGNORED_TABLES]

    out: dict = {}
    for table in tables:
        cur.execute(f'PRAGMA table_info("{table}")')
        columns = sorted(
            (r[1], (r[2] or "").upper(), bool(r[3]), (r[4] or "").strip(), r[5])
            for r in cur.fetchall()
        )

        cur.execute(f'PRAGMA foreign_key_list("{table}")')
        fks = sorted((r[2], r[3], r[4], r[5], r[6]) for r in cur.fetchall())

        cur.execute(f'PRAGMA index_list("{table}")')
        unique, plain = [], []
        for row in cur.fetchall():
            name, is_unique = row[1], bool(row[2])
            if name.startswith("sqlite_autoindex"):
                continue
            cur.execute(f'PRAGMA index_info("{name}")')
            cols = tuple(r[2] for r in cur.fetchall())
            (unique if is_unique else plain).append((name, cols))

        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,))
        ddl = (cur.fetchone()[0] or "").lower()
        named_unique = sorted(
            part.split("unique")[1].strip().split(")")[0] + ")"
            for part in ddl.split("constraint ")
            if " unique" in part
        )

        out[table] = {
            "columns": columns,
            "fks": fks,
            "unique_indexes": sorted(unique),
            "plain_indexes": sorted(plain),
            "named_unique": named_unique,
        }
    con.close()
    return out


def _build_with_create_all(db_path: str) -> None:
    """Fresh subprocess so `settings` picks up our DATABASE_URL.

    The code is passed via a real newline-joined string — `async def` is a
    compound statement and cannot follow a `;` separator.
    """
    code = "\n".join(
        [
            "import asyncio",
            "import app.models  # noqa: F401 - register tables on Base.metadata",
            "from app.db.base import Base",
            "from app.db.session import engine",
            "",
            "async def go():",
            "    async with engine.begin() as conn:",
            "        await conn.run_sync(Base.metadata.create_all)",
            "    await engine.dispose()",
            "",
            "asyncio.run(go())",
        ]
    )
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"},
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def schema_pair(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("schema")
    create_all_db = str(tmp / "create_all.db")
    alembic_db = str(tmp / "alembic.db")

    _build_with_create_all(create_all_db)
    subprocess.run(
        [str(ALEMBIC_BIN), "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{alembic_db}"},
        check=True,
        capture_output=True,
    )
    return _fingerprint(create_all_db), _fingerprint(alembic_db)


def test_migration_and_create_all_agree_on_tables(schema_pair):
    create_all, alembic = schema_pair
    assert set(create_all) == set(alembic), (
        f"table sets differ — create_all only: {set(create_all) - set(alembic)}, "
        f"alembic only: {set(alembic) - set(create_all)}"
    )
    # Compare against the models rather than a hardcoded number, so adding a
    # model does not require editing this test — while a model that the
    # migration forgot still fails loudly.
    expected = set(Base.metadata.tables)
    assert set(create_all) == expected, (
        f"create_all produced {sorted(set(create_all))} but models declare {sorted(expected)}"
    )
    assert len(create_all) >= 12, f"unexpectedly few tables: {len(create_all)}"


@pytest.mark.parametrize(
    "aspect", ["columns", "fks", "unique_indexes", "plain_indexes", "named_unique"]
)
def test_migration_matches_create_all(schema_pair, aspect):
    create_all, alembic = schema_pair
    mismatches = {
        table: (create_all[table][aspect], alembic[table][aspect])
        for table in sorted(set(create_all) & set(alembic))
        if create_all[table][aspect] != alembic[table][aspect]
    }
    assert not mismatches, f"{aspect} drift detected:\n{mismatches}"


def test_alembic_reports_no_pending_model_changes(tmp_path):
    """`alembic check` — models must not contain anything the migration lacks."""
    db = str(tmp_path / "check.db")
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db}"}
    subprocess.run(
        [str(ALEMBIC_BIN), "upgrade", "head"],
        cwd=BACKEND_DIR, env=env, check=True, capture_output=True,
    )
    result = subprocess.run(
        [str(ALEMBIC_BIN), "check"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "alembic check found model changes not captured in a migration:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_downgrade_removes_every_table(tmp_path):
    """`downgrade base` must leave nothing but Alembic's bookkeeping table."""
    db = str(tmp_path / "down.db")
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db}"}
    subprocess.run(
        [str(ALEMBIC_BIN), "upgrade", "head"],
        cwd=BACKEND_DIR, env=env, check=True, capture_output=True,
    )
    subprocess.run(
        [str(ALEMBIC_BIN), "downgrade", "base"],
        cwd=BACKEND_DIR, env=env, check=True, capture_output=True,
    )
    con = sqlite3.connect(db)
    left = [
        n for (n,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    con.close()
    assert left == ["alembic_version"], f"downgrade left tables behind: {left}"
