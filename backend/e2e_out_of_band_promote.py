"""Out-of-band ADMIN grant, for E2E runs in an environment that blocks child processes.

Generated from `frontend/e2e/out-of-band-roles.ts` (keep the two in step).

Why this exists
---------------
`frontend/e2e/helpers.ts::promoteToAdmin` drives `scripts/manage_roles.py`, which
is the honest path: there is deliberately no HTTP route that grants a role, so a
test that becomes an administrator should use the CLI. But inside the WorkBuddy
sandbox **every** child process spawn fails with `EBUSY` -- measured with
`node --version` and `bash -c echo` as controls, so it is the sandbox and not the
interpreter path or the argument list.

This script performs the same UPDATE the CLI performs, with the same guard rails,
but reads the phone list from the environment so it can be driven
non-interactively. A green admin run under this path proves the admin **UI** and
the audit trail work; it proves **nothing** about `manage_roles.py`. Those
guarantees live in `backend/tests/`, which covers the CLI directly.

Usage (the only supported invocation -- always point at the E2E database):

    cd backend
    ENV=development \
    DATABASE_URL=sqlite+aiosqlite:///./e2e.db \
    E2E_ADMIN_PHONES="+85290000001" \
    C:/Users/user/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe \
      -m e2e_out_of_band_promote

Refuses to run against a database that is not the throwaway E2E file, because
"accidentally grant admin on a real database" is the failure mode this kind of
helper invites.
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.enums import UserRole
from app.models.user import User

E2E_DB_MARKER = "e2e.db"


async def main() -> int:
    phones = [p.strip() for p in os.environ.get("E2E_ADMIN_PHONES", "").split(",") if p.strip()]
    if not phones:
        print("E2E_ADMIN_PHONES is empty; nothing to do")
        return 0

    print(f"target database: {settings.DATABASE_URL}")
    if E2E_DB_MARKER not in settings.DATABASE_URL:
        print(
            f"refusing to run: {settings.DATABASE_URL!r} does not look like the "
            f"throwaway E2E database (expected a path containing {E2E_DB_MARKER!r})",
            file=sys.stderr,
        )
        return 3

    granted: list[str] = []
    missing: list[str] = []
    async with AsyncSessionLocal() as db:
        for phone in phones:
            user = (
                await db.execute(select(User).where(User.phone_number == phone))
            ).scalar_one_or_none()
            if user is None:
                missing.append(phone)
                continue
            if user.is_deleted:
                missing.append(f"{phone} (deleted)")
                continue
            if user.role != UserRole.ADMIN:
                user.role = UserRole.ADMIN
            granted.append(phone)
        await db.commit()

    print(f"granted: {granted}")
    if missing:
        print(f"not found / ineligible: {missing}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
