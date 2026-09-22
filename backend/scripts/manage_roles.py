"""Grant or revoke the ADMIN role.

Why this exists as a script rather than an endpoint: there is deliberately no HTTP
route that can change a role. An endpoint that grants admin is a privilege
escalation waiting to be misconfigured, and it is not needed — an operator with
database access can run this. **Nothing here is reachable over the network.**

It also fills a real gap: without it there is *no* way to create the first
administrator, which is why the admin surface had no browser test coverage either.

Usage
-----
    python scripts/manage_roles.py list
    python scripts/manage_roles.py promote +85291234567
    python scripts/manage_roles.py demote  +85291234567 [--force]

The target database is printed on every run. Pointing this at the wrong
`DATABASE_URL` would silently grant admin on production, so the operator should
see which database they are about to change before it happens.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.user import User  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_FOUND = 2

#: Redacted so a connection string with an embedded password is never printed.
def _describe_target() -> str:
    url = settings.DATABASE_URL
    if "@" in url:  # scheme://user:password@host/db
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


async def _find(db, phone_number: str) -> User | None:
    return (
        await db.execute(select(User).where(User.phone_number == phone_number))
    ).scalar_one_or_none()


async def _admin_count(db, *, exclude_id=None) -> int:
    stmt = select(func.count()).select_from(User).where(
        # `deleted_at IS NULL`, not `is_deleted` — the latter is a Python
        # @property on SoftDeleteMixin and cannot be used in a query.
        User.role == UserRole.ADMIN,
        User.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    return int(await db.scalar(stmt) or 0)


async def cmd_list() -> int:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(User.phone_number, User.is_active, User.deleted_at)
                .where(User.role == UserRole.ADMIN)
                .order_by(User.created_at)
            )
        ).all()

    if not rows:
        print("No administrators. Nobody can reach the admin surface.")
        return EXIT_OK

    print(f"{len(rows)} administrator(s):")
    for phone, is_active, deleted_at in rows:
        flags = []
        if not is_active:
            flags.append("inactive")
        if deleted_at is not None:
            flags.append("deleted")
        # A deleted or inactive admin cannot actually log in, so saying so here
        # saves an operator from wondering why the account "is an admin" but 403s.
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  {phone}{suffix}")
    return EXIT_OK


async def cmd_promote(phone_number: str) -> int:
    async with AsyncSessionLocal() as db:
        user = await _find(db, phone_number)
        if user is None:
            print(f"No user with phone {phone_number}.", file=sys.stderr)
            return EXIT_NOT_FOUND
        if user.role == UserRole.ADMIN:
            print(f"{phone_number} is already an administrator. Nothing to do.")
            return EXIT_OK
        if user.is_deleted:
            print(
                f"Refusing: {phone_number} is deleted. Promoting it would create an "
                "administrator that cannot log in.",
                file=sys.stderr,
            )
            return EXIT_ERROR

        user.role = UserRole.ADMIN
        await db.commit()
        remaining = await _admin_count(db)

    print(f"{phone_number} is now an administrator. ({remaining} total)")
    return EXIT_OK


async def cmd_demote(phone_number: str, *, force: bool) -> int:
    async with AsyncSessionLocal() as db:
        user = await _find(db, phone_number)
        if user is None:
            print(f"No user with phone {phone_number}.", file=sys.stderr)
            return EXIT_NOT_FOUND
        if user.role != UserRole.ADMIN:
            print(f"{phone_number} is not an administrator. Nothing to do.")
            return EXIT_OK

        others = await _admin_count(db, exclude_id=user.id)
        if others == 0 and not force:
            # Locking everyone out of the admin surface is recoverable only by
            # running this script again, which is exactly the situation an
            # operator is least likely to be able to do. Require intent.
            print(
                f"Refusing: {phone_number} is the last administrator. Demoting it "
                "would leave nobody able to review reports or read the audit trail.\n"
                "Re-run with --force if that is genuinely what you want.",
                file=sys.stderr,
            )
            return EXIT_ERROR

        user.role = UserRole.USER
        await db.commit()
        remaining = await _admin_count(db)

    print(f"{phone_number} is no longer an administrator. ({remaining} total)")
    if remaining == 0:
        print("Warning: there are now no administrators.", file=sys.stderr)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Grant or revoke the ADMIN role. Not reachable over HTTP."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list current administrators")

    promote = sub.add_parser("promote", help="grant ADMIN to a phone number")
    promote.add_argument("phone_number")

    demote = sub.add_parser("demote", help="revoke ADMIN from a phone number")
    demote.add_argument("phone_number")
    demote.add_argument(
        "--force",
        action="store_true",
        help="allow removing the last administrator (locks everyone out)",
    )

    args = parser.parse_args(argv)

    print(f"Target: {_describe_target()}  (env={settings.ENV})")

    if args.command == "list":
        return asyncio.run(cmd_list())
    if args.command == "promote":
        return asyncio.run(cmd_promote(args.phone_number))
    return asyncio.run(cmd_demote(args.phone_number, force=args.force))


if __name__ == "__main__":
    raise SystemExit(main())
