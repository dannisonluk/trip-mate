"""notification code + params replace the server-rendered title

Revision ID: 9c4e6f80a1b3
Revises: 3a7b9c14e5f2
Create Date: 2026-09-26 23:50:00.000000

`notifications.title` held a sentence already rendered in Traditional Chinese.
That freezes one language into the row: an English-locale recipient reads
`Alice 接受了你的申請：東京櫻花季攝影之旅` and no frontend change can recover the
sentence, because the sentence no longer exists in a language-neutral form. See
`docs/AUDIT-2026-09-26.md` (B6).

This splits the row into a stable `code`, a JSON `params` bag to interpolate,
and the optional `body` preview — and **backfills the code from the old title**
so pre-existing rows keep rendering instead of turning into blank cards.

Backfill fidelity is deliberate but partial: the old `title` cannot be inverted
into exact params (the nickname and the trip title are embedded mid-sentence in
a language-specific word order), so the migration parses what it can and falls
back to a `legacy` code carrying the original string as a single parameter.
Those rows keep their original language — which is the honest outcome, since
their params were never recorded.

`title` is dropped rather than kept nullable: leaving it would allow a future
writer to resurrect the defect, and the erasure path would have to scrub a
second copy of the same free text.
"""
from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = '9c4e6f80a1b3'
down_revision = '3a7b9c14e5f2'
branch_labels = None
depends_on = None


#: Old `NotificationType` -> the code the client resolves. One-to-one today,
#: which is why the backfill can be a plain mapping rather than a parse.
_TYPE_TO_CODE = {
    'APPLICATION_RECEIVED': 'application_received',
    'APPLICATION_ACCEPTED': 'application_accepted',
    'APPLICATION_REJECTED': 'application_rejected',
    'NEW_MESSAGE': 'new_message',
    'REVIEW_RECEIVED': 'review_received',
}

#: Used when the old row carried a code we cannot reconstruct. The original
#: sentence travels in `params.text` and the client renders it verbatim.
_LEGACY_CODE = 'legacy'


def _rows(bind):
    return bind.execute(
        sa.text('SELECT id, type, title, body FROM notifications')
    ).fetchall()


def upgrade() -> None:
    bind = op.get_bind()

    # Captured *before* the drop, obviously — the whole point is that this data
    # is the only remaining record of what those rows said.
    existing = _rows(bind)

    op.add_column(
        'notifications',
        sa.Column('code', sa.String(length=60), nullable=True),
    )
    op.add_column(
        'notifications',
        sa.Column('params', sa.JSON(), nullable=True),
    )

    # Backfill before the NOT NULL, so adding the constraint cannot fail on a
    # table that already has rows.
    for row in existing:
        code = _TYPE_TO_CODE.get(row.type, _LEGACY_CODE)
        # The preview is genuine content and stays in `body`; the reconstructed
        # sentence goes into params so the client can show it until the row ages
        # out. Two parameters, both strings, which is all the client needs.
        params = {'text': row.title}
        bind.execute(
            sa.text(
                'UPDATE notifications SET code = :code, params = :params WHERE id = :id'
            ),
            {'code': code, 'params': json.dumps(params), 'id': row.id},
        )

    with op.batch_alter_table('notifications') as batch:
        batch.alter_column('code', existing_type=sa.String(length=60), nullable=False)
        batch.drop_column('title')


def downgrade() -> None:
    bind = op.get_bind()

    op.add_column(
        'notifications',
        sa.Column('title', sa.String(length=120), nullable=True),
    )

    # Best effort: recover the readable string we have. Rows written after the
    # upgrade have only `code` + `params`, so a downgrade that lands on real
    # data produces a title of the code — visibly wrong rather than silently
    # blank, which is what an operator needs to notice.
    for row in bind.execute(
        sa.text('SELECT id, code, params FROM notifications')
    ).fetchall():
        params = row.params
        if isinstance(params, (str, bytes)):
            try:
                params = json.loads(params)
            except (TypeError, ValueError):
                params = None
        text = (params or {}).get('text') if isinstance(params, dict) else None
        bind.execute(
            sa.text('UPDATE notifications SET title = :title WHERE id = :id'),
            {'title': text or row.code or '', 'id': row.id},
        )

    with op.batch_alter_table('notifications') as batch:
        batch.alter_column('title', existing_type=sa.String(length=120), nullable=False)
        batch.drop_column('params')
        batch.drop_column('code')
