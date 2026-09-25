"""review unique per untripped pair

Revision ID: 8f41d2b7c6aa
Revises: 2cc02c1a66dd
Create Date: 2026-09-26 21:40:12.000000

`uq_review_once_per_trip` is `(reviewer_id, reviewee_id, trip_post_id)`, and SQL
treats NULLs as distinct — so two rows of `(a, b, NULL)` do not collide and the
constraint is inert for exactly the reviews that are not scoped to a trip. A
pair could accumulate unlimited untripped reviews, and the duplicate check in
`create_review` (a `SELECT` then an `INSERT`) could not protect against a
concurrent pair of requests even for tripped ones.

This adds a partial unique index covering only `trip_post_id IS NULL`. It is
partial rather than a full index on `(reviewer_id, reviewee_id)` because that
would be *wrong*: the same pair legitimately reviews each other once per shared
trip.

Upgrade order matters. The constraint is created **after** the backfill, so an
existing deployment that already contains duplicates fails loudly at the
duplicate-detection step with the offending pairs named, rather than at
`CREATE INDEX` with an opaque database error. Deleting the extras is not
something a migration should decide on its own — it is somebody's review — so
the upgrade refuses and tells the operator what to remove. See
`docs/AUDIT-2026-09-26.md` (B1).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '8f41d2b7c6aa'
down_revision = '2cc02c1a66dd'
branch_labels = None
depends_on = None

INDEX_NAME = 'uq_review_once_per_untripped_pair'

_DUPLICATE_QUERY = sa.text(
    """
    SELECT reviewer_id, reviewee_id, COUNT(*) AS n
    FROM reviews
    WHERE trip_post_id IS NULL
    GROUP BY reviewer_id, reviewee_id
    HAVING COUNT(*) > 1
    """
)


def _find_duplicates(bind):
    """Colliding `(reviewer_id, reviewee_id)` pairs already in the table."""
    return bind.execute(_DUPLICATE_QUERY).fetchall()


def upgrade() -> None:
    bind = op.get_bind()

    duplicates = _find_duplicates(bind)
    if duplicates:
        # Refuse rather than delete: the extra rows are genuine user content and
        # which one to keep is not the migration's call.
        described = ', '.join(
            f'{row[0]}/{row[1]} ({row[2]} rows)' for row in duplicates[:20]
        )
        more = '' if len(duplicates) <= 20 else f' …and {len(duplicates) - 20} more'
        raise RuntimeError(
            'Cannot add a unique constraint on untripped reviews: '
            f'{len(duplicates)} pair(s) already hold duplicates — {described}{more}. '
            'Remove the surplus review rows, then re-run this migration.'
        )

    # `IF NOT EXISTS` is not portable across the dialects; a plain create is
    # correct because the duplicate probe above has already passed and the index
    # name is new.
    op.create_index(
        INDEX_NAME,
        'reviews',
        ['reviewer_id', 'reviewee_id'],
        unique=True,
        postgresql_where=sa.text('trip_post_id IS NULL'),
        sqlite_where=sa.text('trip_post_id IS NULL'),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name='reviews')
