"""Reference table of canonical cities, imported from GeoNames.

Why a table instead of a geocoding API call
-------------------------------------------
The product rule is that a destination city must be a **canonical name** chosen
from a list, not free text. Free text cannot be matched reliably: "Osaka",
"osaka" and "大阪" are three different strings, and the matching engine compares
strings literally (`services/matching.py`), so a user who typed one spelling
would silently never match a trip that used another.

Google's Geocoding API cannot serve this need — its terms cap caching at 30
days, require the result to be displayed on Google Maps, and forbid bulk
geocoding to build a stored dataset. A local, offline dataset has none of those
constraints and no runtime failure mode.

Source and licence
------------------
GeoNames `cities5000.zip` (<https://download.geonames.org/export/dump/>),
**CC BY 4.0**. Attribution is required and is surfaced in the UI; see
`docs/ATTRIBUTION.md`.

Only ten of the nineteen source columns are kept. The dropped ones are either
irrelevant (`cc2`, `elevation`, `dem`) or actively wasteful — `alternatenames`
runs to 10,000 characters per row and would multiply the table size for data
nothing reads.
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: Longest `name` in cities5000 is 97 characters (a Canadian township whose name
#: lists nine constituent townships). `String(80)`, used elsewhere in this
#: codebase for user-supplied country/city text, would silently truncate it on
#: PostgreSQL. Sized for the data, not for the form field.
CITY_NAME_MAX = 100
#: Longest country display name in `countryInfo.txt` is 44 characters.
COUNTRY_NAME_MAX = 80
#: Longest admin1 name measured across the 3,865 entries of `admin1CodesASCII.txt`.
ADMIN1_NAME_MAX = 100


class City(Base):
    """A canonical, selectable city.

    Rows are reference data: they are written only by `scripts/import_cities.py`
    and never by the application. There is no `TimestampMixin` — an `updated_at`
    that nothing ever updates is a lie about the table's guarantees.
    """

    __tablename__ = "cities"

    #: GeoNames `geonameid`. Kept as the primary key rather than minting a UUID:
    #: it is already unique and stable, and it is the join key back to the source
    #: dataset, which makes re-importing a correction a plain upsert. Our other
    #: tables use UUIDs because *we* create their rows and must not leak a
    #: guessable sequence; this table is public reference data with nothing to
    #: enumerate.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    #: Display name, with diacritics (`Sant Julià de Lòria`).
    name: Mapped[str] = mapped_column(String(CITY_NAME_MAX), nullable=False)
    #: Unaccented form (`Sant Julia de Loria`). Searching matches against **this**
    #: column, not `name`: 14,419 of the display names carry diacritics, and a
    #: user without a keyboard for them types the plain spelling. Because the
    #: search is a case-insensitive prefix match, the column carries
    #: `COLLATE NOCASE` so the B-tree is actually usable by `LIKE` — see
    #: `__table_args__` for the measurements.
    asciiname: Mapped[str] = mapped_column(
        String(CITY_NAME_MAX, collation="NOCASE"), nullable=False
    )

    #: ISO-3166 alpha-2 of the city's country/territory.
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    #: `countryInfo.txt` display name, denormalised at import so a listing query
    #: needs no second lookup. `country_code` remains the source of truth.
    country_name: Mapped[str] = mapped_column(String(COUNTRY_NAME_MAX), nullable=False)
    #: Raw admin1 code (`JP.32`). `NULL` for the 51 rows that have none.
    admin1_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: Resolved admin1 label (`Osaka`). `NULL` when the code does not resolve —
    #: 40 (country, admin1) pairs in the dataset are `00`, meaning "no specific
    #: admin division", as used by city-states such as Hong Kong and Singapore.
    admin1_name: Mapped[str | None] = mapped_column(String(ADMIN1_NAME_MAX), nullable=True)

    #: WGS-84. **City-centre coordinates only**, never anything a user supplied.
    #: Storing these is what the map layer needs; the privacy rule is preserved
    #: by the granularity (a city centre is knowable from the city name alone)
    #: and by the fact that no coordinate ever enters the database from a client.
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    #: Used to rank suggestions. `BigInteger` because the column is `bigint` in
    #: the source and population can exceed the 32-bit `Integer` range.
    population: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: GeoNames feature code: `PPLC` capital, `PPLA*` admin seat, `PPL` otherwise.
    #: Retained so notability can be expressed in ranking as well as population.
    feature_code: Mapped[str] = mapped_column(String(10), nullable=False)
    #: IANA zone (`Asia/Tokyo`). Unused today; kept because it is the one source
    #: column with no other route back into the database (unlike, say, admin2,
    #: which can be re-derived from a re-import).
    timezone: Mapped[str | None] = mapped_column(String(40), nullable=True)

    __table_args__ = (
        # Deliberately **no** uniqueness constraint on `name`. The same name
        # legitimately appears in many places — `Santa Cruz` occurs 16 times,
        # `Richmond` 15 — and a unique name index would reject real cities in
        # order to make a query nobody runs.
        #
        # Search is a case-insensitive prefix match on the *unaccented* name, so
        # the index is a plain B-tree on `asciiname`, which carries
        # `COLLATE NOCASE`. This was measured, not assumed, because the naive
        # form silently degrades:
        #
        #   WHERE name LIKE 'osa%'                    -> full table SCAN
        #   WHERE asciiname LIKE 'osa%'               -> index range SEARCH
        #
        # without the collation (SQLite's `LIKE` is case-insensitive for ASCII,
        # so it cannot use a case-sensitive B-tree): 1,350 ms versus 30 ms over
        # 200 lookups against the 69,740-row table — 44x, and the gap grows with
        # the table.
        #
        # The collation lives on the column rather than on the index expression
        # because SQLAlchemy cannot reliably diff an expression index: autogenerate
        # reports the stored index as column-less and `alembic check` then fails
        # forever on phantom drift. With the collation on the column, metadata and
        # the database agree and the drift check stays meaningful.
        Index("ix_cities_asciiname_nocase", "asciiname"),
        # Rank suggestions within a country, and let the frontend filter by
        # country once one is chosen.
        Index("ix_cities_country_population", "country_code", "population"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<City {self.id} {self.name} ({self.country_code})>"
