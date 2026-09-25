"""add cities reference table and city_id links

Revision ID: 2cc02c1a66dd
Revises: 619d0806a0cd
Create Date: 2026-09-23 07:17:42.978820

Adds the canonical city reference table (imported from GeoNames `cities5000`)
and links both places that name a city to it.

Why `city_id` is an integer FK and not the city text
---------------------------------------------------
The map layer needs coordinates, and `docs/SECURITY.md` §2.2 previously stated
that the schema stores **no** coordinates at all. Storing them on `trip_posts`
would have let a client-adjacent value determine a location, so instead a trip
points at a row in a table the application cannot write: the coordinate is a
property of a *city*, not of a trip. A trip that has no `city_id` simply has no
map, which is the correct outcome for "user declined to pick a city".

Both `city_id` columns are nullable and use `ondelete="SET NULL"`:

* nullable, because the city is an optional field — a trip to "Japan" with no
  city is legitimate, and forcing a city would be a worse product;
* `SET NULL` rather than `CASCADE`, because these are *reference* rows. A
  cascade would mean that re-importing GeoNames could **delete user trips** —
  the most dangerous possible failure direction for a data refresh.

`cities.id` is GeoNames' `geonameid`, not a generated UUID like every other
table in this project. Those UUIDs exist so we do not leak a guessable
enumeration of *our* rows; this table holds public reference data with nothing
to enumerate, and keeping the upstream id makes a re-import an upsert.

Named constraints
-----------------
Autogenerate emitted `batch_op.create_foreign_key(None, ...)` — and on SQLite
that form **cannot run at all**: batch mode rewrites the whole table, so every
constraint it adds must carry a name, and `add_constraint` raises
`ValueError: Constraint must have a name` before any DDL is emitted. So the
"please adjust" comment autogenerate leaves behind is not optional advice here;
leaving it would have made the upgrade fail outright. (Measured, not assumed:
rewriting this file to the unnamed form and running `alembic upgrade head`
against a scratch database reproduces exactly that error.)

The matching `drop_constraint(None, ...)` would have broken the rollback path
independently. Both foreign keys are therefore named explicitly.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '2cc02c1a66dd'
down_revision = '619d0806a0cd'
branch_labels = None
depends_on = None

#: Named so `downgrade()` can drop them. SQLite in particular cannot drop an
#: unnamed constraint, and batch mode needs the name to rebuild the table.
_FK_TRIP_POSTS = 'fk_trip_posts_city_id_cities'
_FK_TRAVEL_HISTORIES = 'fk_travel_histories_city_id_cities'


def upgrade() -> None:
    op.create_table(
        'cities',
        sa.Column('id', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        # `COLLATE NOCASE` is what makes the search index usable, not decoration.
        sa.Column('asciiname', sa.String(length=100, collation='NOCASE'), nullable=False),
        sa.Column('country_code', sa.String(length=2), nullable=False),
        sa.Column('country_name', sa.String(length=80), nullable=False),
        sa.Column('admin1_code', sa.String(length=20), nullable=True),
        sa.Column('admin1_name', sa.String(length=100), nullable=True),
        sa.Column('latitude', sa.Float(), nullable=False),
        sa.Column('longitude', sa.Float(), nullable=False),
        sa.Column('population', sa.BigInteger(), nullable=False),
        sa.Column('feature_code', sa.String(length=10), nullable=False),
        sa.Column('timezone', sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # These two indexes are what the suggestion query depends on:
    #   ix_cities_asciiname_nocase   -> case-insensitive prefix match
    #   ix_cities_country_population -> rank within a chosen country
    #
    # The search index is a plain B-tree on `asciiname`, which is declared
    # `COLLATE NOCASE` above. The collation is what makes the index usable and it
    # has to be on the column: `LIKE` is case-insensitive for ASCII in SQLite, so
    # a case-sensitive B-tree cannot serve it, and the query degrades to a full
    # scan. Measured against the imported 69,740-row table:
    #
    #   asciiname COLLATE NOCASE : index range SEARCH, 30 ms / 200 lookups
    #   plain case-sensitive     : full table SCAN,  1,350 ms / 200 lookups
    #
    # An expression index (`ON cities (asciiname COLLATE NOCASE)`) would also
    # work at runtime, but SQLAlchemy cannot diff one — autogenerate reports the
    # stored index as column-less and `alembic check` then fails on permanent
    # phantom drift. Declaring the collation on the column keeps metadata and the
    # database in agreement.
    op.create_index('ix_cities_asciiname_nocase', 'cities', ['asciiname'], unique=False)
    op.create_index(
        'ix_cities_country_population', 'cities', ['country_code', 'population'], unique=False
    )

    with op.batch_alter_table('travel_histories', schema=None) as batch_op:
        batch_op.add_column(sa.Column('city_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_travel_histories_city_id'), ['city_id'], unique=False)
        batch_op.create_foreign_key(
            _FK_TRAVEL_HISTORIES, 'cities', ['city_id'], ['id'], ondelete='SET NULL'
        )

    with op.batch_alter_table('trip_posts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('city_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_trip_posts_city_id'), ['city_id'], unique=False)
        batch_op.create_foreign_key(
            _FK_TRIP_POSTS, 'cities', ['city_id'], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    with op.batch_alter_table('trip_posts', schema=None) as batch_op:
        batch_op.drop_constraint(_FK_TRIP_POSTS, type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_trip_posts_city_id'))
        batch_op.drop_column('city_id')

    with op.batch_alter_table('travel_histories', schema=None) as batch_op:
        batch_op.drop_constraint(_FK_TRAVEL_HISTORIES, type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_travel_histories_city_id'))
        batch_op.drop_column('city_id')

    op.drop_index('ix_cities_country_population', table_name='cities')
    op.drop_index('ix_cities_asciiname_nocase', table_name='cities')
    op.drop_table('cities')
