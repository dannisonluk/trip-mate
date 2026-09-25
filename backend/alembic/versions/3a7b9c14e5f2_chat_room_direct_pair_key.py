"""chat room direct pair key

Revision ID: 3a7b9c14e5f2
Revises: 8f41d2b7c6aa
Create Date: 2026-09-26 22:15:00.000000

`DIRECT` rooms were deduplicated only by comparing the two member sets in
Python, so nothing stopped two concurrent requests from both finding no room and
both inserting one. A pair then had two rooms, each holding half the
conversation, with no way to tell which was real.

This adds `direct_pair_key` — the two profile ids sorted and colon-joined — and
a partial unique index over it, so the database enforces "one DIRECT room per
pair" instead of a read-then-write check that a race can slip past.

**Backfill before constraint**, per this project's migration rule. Duplicates
are *merged* rather than rejected, because unlike a duplicate review (which is
somebody's opinion, and which one to keep is not the migration's call) a
duplicate room is pure duplication: the correct outcome is one room holding all
the messages and both members. The surviving room is the **oldest** — it is the
one whichever request arrived first would have returned, and it is the one whose
id a client is most likely to have already stored.

Messages and members are moved, not deleted; only then is the surplus room row
removed. TRIP rooms keep `direct_pair_key = NULL` and are untouched.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '3a7b9c14e5f2'
down_revision = '8f41d2b7c6aa'
branch_labels = None
depends_on = None

INDEX_NAME = 'uq_chat_room_direct_pair'


def _pair_key(a: str, b: str) -> str:
    """Duplicated from `app.models.chat.direct_pair_key` on purpose.

    A migration must keep producing the same rows even if the application helper
    is renamed or its separator changes — so it does not import it. Same rule the
    tag normaliser follows in `cafc59ae9ecc`.
    """
    return ":".join(sorted([str(a), str(b)]))


def _direct_rooms(bind):
    """Every DIRECT room with its member ids, oldest first."""
    rows = bind.execute(
        sa.text(
            """
            SELECT r.id, r.created_at,
                   (SELECT GROUP_CONCAT(m.profile_id)
                      FROM chat_room_members m
                     WHERE m.room_id = r.id) AS member_ids,
                   (SELECT COUNT(*)
                      FROM chat_room_members m
                     WHERE m.room_id = r.id) AS member_count
              FROM chat_rooms r
             WHERE r.room_type = 'DIRECT'
             ORDER BY r.created_at, r.id
            """
        )
    ).fetchall()
    return rows


def upgrade() -> None:
    bind = op.get_bind()

    with op.batch_alter_table("chat_rooms", schema=None) as batch_op:
        batch_op.add_column(sa.Column("direct_pair_key", sa.String(length=73), nullable=True))

    # --- backfill, and merge any duplicates the race already produced --------
    groups: dict[str, list[str]] = {}
    for room_id, _created, member_ids, member_count in _direct_rooms(bind):
        if member_count != 2 or not member_ids:
            # A DIRECT room with the wrong number of members has no well-defined
            # pair and cannot be keyed. Leave it NULL: it is a pre-existing
            # anomaly, and inventing a key would silently adopt it into the
            # constraint's protection.
            continue
        ids = str(member_ids).split(",")
        key = _pair_key(ids[0], ids[1])
        groups.setdefault(key, []).append(str(room_id))

    merged = 0
    for key, room_ids in groups.items():
        # Oldest first, so `room_ids[0]` is the survivor.
        keeper, surplus = room_ids[0], room_ids[1:]
        bind.execute(
            sa.text("UPDATE chat_rooms SET direct_pair_key = :key WHERE id = :id"),
            {"key": key, "id": keeper},
        )
        for dead in surplus:
            # Move the conversation into the survivor before removing the room,
            # or the messages would go with the cascade.
            bind.execute(
                sa.text("UPDATE chat_messages SET room_id = :keeper WHERE room_id = :dead"),
                {"keeper": keeper, "dead": dead},
            )
            # A member row could collide with one already in the survivor
            # (`uq_room_member`); drop the redundant row and keep the original.
            bind.execute(
                sa.text(
                    "DELETE FROM chat_room_members WHERE room_id = :dead AND profile_id IN "
                    "(SELECT profile_id FROM chat_room_members WHERE room_id = :keeper)"
                ),
                {"keeper": keeper, "dead": dead},
            )
            bind.execute(
                sa.text("UPDATE chat_room_members SET room_id = :keeper WHERE room_id = :dead"),
                {"keeper": keeper, "dead": dead},
            )
            bind.execute(sa.text("DELETE FROM chat_rooms WHERE id = :dead"), {"dead": dead})
            merged += 1

    if merged:
        print(f"[3a7b9c14e5f2] merged {merged} duplicate DIRECT room(s) into their oldest twin")

    op.create_index(
        INDEX_NAME,
        "chat_rooms",
        ["direct_pair_key"],
        unique=True,
        postgresql_where=sa.text("direct_pair_key IS NOT NULL"),
        sqlite_where=sa.text("direct_pair_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="chat_rooms")
    with op.batch_alter_table("chat_rooms", schema=None) as batch_op:
        batch_op.drop_column("direct_pair_key")
