"""Block-list / anti-harassment enforcement (§3.2).

A block is directional in storage but symmetric in effect: if A blocks B *or*
B blocks A, neither party may message, apply to, or comment on the other.
"""
import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.moderation import Block


def _as_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def is_blocked_between(db: AsyncSession, profile_a, profile_b) -> bool:
    a, b = _as_uuid(profile_a), _as_uuid(profile_b)
    if a == b:
        return False
    stmt = select(Block.id).where(
        or_(
            and_(Block.blocker_profile_id == a, Block.blocked_profile_id == b),
            and_(Block.blocker_profile_id == b, Block.blocked_profile_id == a),
        )
    )
    return (await db.execute(stmt)).first() is not None


async def blocked_profile_ids(db: AsyncSession, profile_id) -> set[uuid.UUID]:
    """Profiles that must be hidden from, and cannot interact with, `profile_id`."""
    pid = _as_uuid(profile_id)
    stmt = select(Block.blocker_profile_id, Block.blocked_profile_id).where(
        or_(Block.blocker_profile_id == pid, Block.blocked_profile_id == pid)
    )
    result: set[uuid.UUID] = set()
    for blocker, blocked in (await db.execute(stmt)).all():
        result.add(blocked if blocker == pid else blocker)
    return result


async def ensure_not_blocked(db: AsyncSession, actor_profile_id, target_profile_id) -> None:
    """Raise PermissionError if interaction is forbidden by a block."""
    if await is_blocked_between(db, actor_profile_id, target_profile_id):
        raise PermissionError("Interaction blocked by user preference.")


async def block_profile(
    db: AsyncSession, blocker_profile_id, blocked_profile_id
) -> tuple[Block, bool]:
    """Create the block (idempotent) and flush. Does **not** commit.

    Returns `(block, created)`. `created` is False when the block already
    existed, which the caller needs in order to avoid recording a state change
    that did not happen — the same rule the unblock path already follows. Without
    it, blocking an already-blocked profile appends a second `USER_BLOCKED` row
    for an event that never occurred, and an audit trail that reports events that
    did not happen is worse than one with gaps.

    The caller owns the transaction, so the audit entry for this action lands in
    the same commit as the block itself. A block recorded without its audit row —
    or an audit row describing a block that rolled back — would each be a lie,
    and only a shared transaction rules both out.

    `flush()` rather than nothing because the returned `Block` is serialised
    immediately: it needs its generated `id` and `created_at`, which the
    Python-side defaults only populate on flush.

    **Concurrency.** The read-then-insert is not atomic, so two simultaneous
    blocks of the same pair both pass the `SELECT` and one loses the `INSERT` to
    `uq_block_pair`. That loser is handled rather than propagated: it re-reads
    the winner's row and reports it with `created=False`, which is the same
    answer the request would have received a moment later. The alternative —
    letting the `IntegrityError` escape — made the second of two identical
    requests return 500 while the first returned 201, for a purely idempotent
    operation.
    """
    blocker, blocked = _as_uuid(blocker_profile_id), _as_uuid(blocked_profile_id)
    if blocker == blocked:
        raise ValueError("You cannot block yourself.")

    existing = (
        await db.execute(
            select(Block).where(
                Block.blocker_profile_id == blocker, Block.blocked_profile_id == blocked
            )
        )
    ).scalars().first()
    if existing:
        return existing, False

    block = Block(blocker_profile_id=blocker, blocked_profile_id=blocked)
    db.add(block)
    try:
        await db.flush()
    except IntegrityError:
        # `uq_block_pair` fired: a concurrent request (a double-clicked block
        # button, or two tabs) inserted the same pair between our SELECT and our
        # INSERT. That is not an error — the caller's intent is already
        # satisfied, and the answer is the idempotent one it would have received
        # had the other request landed a moment earlier.
        #
        # Without this the `IntegrityError` propagated as a 500, so the *second*
        # of two identical requests failed while the first succeeded. The
        # rollback is mandatory: the session is unusable until the failed
        # transaction is discarded, and the re-read below has to run on a clean
        # one.
        await db.rollback()
        existing = (
            await db.execute(
                select(Block).where(
                    Block.blocker_profile_id == blocker,
                    Block.blocked_profile_id == blocked,
                )
            )
        ).scalars().first()
        if existing is not None:
            return existing, False
        # The row is genuinely gone — somebody unblocked between the failed
        # insert and this read. Reporting a block that does not exist would be a
        # lie, so surface the original failure rather than inventing success.
        raise

    return block, True


async def unblock_profile(db: AsyncSession, blocker_profile_id, blocked_profile_id) -> bool:
    """Remove the block, if any. Does **not** commit — see `block_profile`."""
    blocker, blocked = _as_uuid(blocker_profile_id), _as_uuid(blocked_profile_id)
    block = (
        await db.execute(
            select(Block).where(
                Block.blocker_profile_id == blocker, Block.blocked_profile_id == blocked
            )
        )
    ).scalars().first()
    if not block:
        return False
    await db.delete(block)
    await db.flush()
    return True
