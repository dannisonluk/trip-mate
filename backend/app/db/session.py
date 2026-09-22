"""Async database engine / session factory (spec: SQLAlchemy 2.0 Async)."""
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.base import Base

_engine_kwargs: dict = {"pool_pre_ping": True, "echo": settings.DB_ECHO}
IS_SQLITE = settings.DATABASE_URL.startswith("sqlite")
if IS_SQLITE:
    # SQLite + aiosqlite: NullPool avoids reusing a connection across event loops
    # (important for tests, where each TestClient spins up its own loop).
    _engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

if IS_SQLITE:

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_enable_foreign_keys(dbapi_connection, connection_record) -> None:
        """Make SQLite actually honour `ON DELETE CASCADE` / `SET NULL`.

        SQLite defaults to `PRAGMA foreign_keys=OFF`, and there is no way to turn
        it on from the URL. Without it every `ondelete=` rule in the models is
        decorative on the dev/test dialect — deleting a trip post would leave its
        applications, tags and notifications behind, while the same operation on
        PostgreSQL (production) would cascade. Silent divergence between the two
        dialects is the exact failure mode that hid the `lazy="raise"` bug, so the
        behaviour is made identical instead of assumed.

        Applied to the app engine only: Alembic runs through its own connection
        (`alembic/env.py`) and batch-mode table rebuilds must keep FKs off.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,   # keep attributes readable after commit (no lazy IO)
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped async session."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Prepare the schema for local dev / tests.

    Production schemas are owned by Alembic. If an `alembic_version` table is
    present we assume migrations are in charge and do nothing, so `create_all`
    can never silently diverge from a migrated database. In production we skip
    entirely — migrations must be applied by the deploy pipeline.
    """
    import logging

    from sqlalchemy import inspect

    from app import models  # noqa: F401  (register all models on Base.metadata)

    logger = logging.getLogger("tripmate.db")

    if settings.is_production:
        logger.info("Production: schema is managed by Alembic — skipping create_all.")
        return

    async with engine.begin() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        if "alembic_version" in tables:
            logger.info("alembic_version present — schema is migration-managed; skipping create_all.")
            return
        await conn.run_sync(Base.metadata.create_all)
