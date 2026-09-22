"""Alembic environment — reads the DB URL from app settings.

The URL is derived from `settings.sync_database_url`, which strips the async
driver (`asyncpg` → `psycopg`, `aiosqlite` → `sqlite`). Alembic always runs
synchronously, so `DATABASE_URL` in the environment should keep its async form.

`render_as_batch=True` is enabled for SQLite so that future ALTER-style
migrations (add/drop column, change type) are rewritten as the
create-copy-swap pattern SQLite actually supports.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401 — registers every table on Base.metadata
from app.core.config import settings
from app.db.base import Base

config = context.config

# Alembic runs synchronously — strip the async driver from the URL.
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# SQLite cannot ALTER most things in place; batch mode rewrites the table.
IS_SQLITE = settings.sync_database_url.startswith("sqlite")


def _common_context_kwargs() -> dict:
    return {
        "target_metadata": target_metadata,
        "compare_type": True,           # detect column type changes
        "compare_server_default": True,  # detect server_default drift
        "render_as_batch": IS_SQLITE,
    }


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sync_database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_common_context_kwargs(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, **_common_context_kwargs())
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
