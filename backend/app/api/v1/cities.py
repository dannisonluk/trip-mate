"""City suggestion router.

    GET /cities?q=osa&country=JP&limit=20

Why this is a prefix search and not a fuzzy one
-----------------------------------------------
`q` is matched as a **prefix** against `cities.asciiname`, which is the
unaccented form. Both halves of that are load-bearing:

* Prefix rather than `%substring%`, because a leading wildcard defeats the index
  — the plan becomes a full scan of all 69,740 rows on every keystroke.
* `asciiname` rather than `name`, because 14,419 display names carry diacritics
  (`Ōsaka`, `Sant Julià de Lòria`) and a user typing plain ASCII has to be able
  to find them. Searching the unaccented column is what makes `osa` match
  `Ōsaki`.

The column is declared `COLLATE NOCASE`, so `LIKE 'osa%'` uses the index
directly. Without that collation SQLite cannot use a case-sensitive B-tree for
its case-insensitive ASCII `LIKE`, and the query silently degrades to a scan —
measured at 44x slower (1,350 ms vs 30 ms per 200 lookups).

Ranking, not filtering
----------------------
Results are ordered by population, with capitals and administrative seats
boosted, and the list is capped. There is no population *threshold*: a small
town is a legitimate destination, it just sorts below a large city for the same
prefix. Truncation is therefore a ranking decision, not an eligibility one.

Why the endpoint requires authentication
----------------------------------------
It is reference data, so this is not about secrecy. An unauthenticated
prefix-search endpoint over a 69,740-row table is a cheap way to make the
database do work, and every other route in this API is authenticated. It is
rate-limited like the other read endpoints.
"""
from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentProfile, DbSession
from app.core.rate_limit import READ_RATE, limit
from app.models.city import City
from app.schemas.city import CityOut, CitySuggestionPage

router = APIRouter(prefix="/cities", tags=["cities"])

#: Below this, a prefix matches so much that the result is not a suggestion —
#: `a` would return 20 arbitrary large cities, which reads as a broken control.
MIN_QUERY_LENGTH = 2
#: Cap on returned suggestions. The picker shows a short list; the client has no
#: use for more, and a larger cap only makes a wider scan worth attempting.
MAX_SUGGESTIONS = 20
DEFAULT_SUGGESTIONS = 10
#: Defensive bound on the term. A 4,000-character prefix is not a search.
MAX_QUERY_LENGTH = 64


def _escape_like(term: str) -> str:
    r"""Escape `LIKE` metacharacters so a typed `%` is a literal, not a wildcard.

    Without this, `q=%` matches every row and `q=_` matches every single-
    character name — the user's input silently becomes query syntax. `\` is the
    escape character and must itself be escaped first, or `\%` would be read as
    an escaped backslash followed by a wildcard.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=CitySuggestionPage)
@limit(READ_RATE)
async def suggest_cities(
    request: Request,
    profile: CurrentProfile,
    db: DbSession,
    q: str = Query(
        ...,
        # `min_length=0`, not 1. An empty `q` is what a client sends when the
        # user clears the input, and that is an ordinary event, not a malformed
        # request — answering 422 would put a validation error in front of
        # someone who simply pressed backspace. It falls into the same
        # empty-list branch as any other too-short term.
        min_length=0,
        max_length=MAX_QUERY_LENGTH,
        description="Prefix of the city name, case- and accent-insensitive",
    ),
    country: str | None = Query(
        default=None,
        min_length=2,
        max_length=2,
        description="Optional ISO-3166 alpha-2 filter, e.g. JP",
    ),
    limit_: int = Query(
        DEFAULT_SUGGESTIONS,
        ge=1,
        le=MAX_SUGGESTIONS,
        alias="limit",
    ),
):
    """Ranked city suggestions for a prefix.

    An empty result is a **200 with an empty list**, never a 404 and never a 422:
    "no city starts with this" is the expected outcome for most typed prefixes,
    and an error status would make the client render a failure state for an
    ordinary keystroke. This includes the empty string, which is what the client
    sends when the user clears the field.

    The picker treats an empty list as "this is not a city in the list", which is
    exactly the product rule — and the corresponding form field is left blank.
    """
    term = q.strip()
    # Case is normalised by the collation, but normalising here keeps the echoed
    # `query` stable and makes the index usable on PostgreSQL too, where the
    # column collation is not `NOCASE`.
    normalised = term.lower()

    if len(normalised) < MIN_QUERY_LENGTH:
        # Not an error, just nothing to suggest yet — the client debounces and
        # will call again on the next keystroke.
        return CitySuggestionPage(query=normalised, suggestions=[])

    pattern = f"{_escape_like(normalised)}%"

    # Boost capitals and administrative seats so `San` tops out with a capital
    # rather than an arbitrary large city. `PPLC` is a national capital; the
    # `PPLA*` family are seats of an administrative division.
    seat_boost = case(
        (City.feature_code == "PPLC", 3),
        (City.feature_code.like("PPLA%"), 2),
        else_=0,
    )

    stmt = (
        select(City)
        .where(_prefix_match(pattern))
        .order_by(seat_boost.desc(), City.population.desc(), City.name.asc())
        .limit(limit_)
    )
    if country:
        stmt = stmt.where(City.country_code == country.upper())

    rows = (await db.execute(stmt)).scalars().all()
    return CitySuggestionPage(
        query=normalised,
        suggestions=[CityOut.model_validate(row) for row in rows],
    )


async def resolve_city_id(db: AsyncSession, city_id: int | None) -> int | None:
    """Return `city_id` if it names a real city, else raise 422.

    A `city_id` that does not resolve is not a cosmetic problem: the trip or
    history would store a location that looks populated in the UI and silently
    never matches anything. That is the same failure the select-only picker
    exists to prevent, so it is checked at the boundary rather than trusted.

    `None` passes through — the field is optional by design.
    """
    if city_id is None:
        return None
    exists = await db.scalar(select(City.id).where(City.id == city_id))
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unknown city_id; choose a city from /cities.",
        )
    return city_id


@router.get("/{city_id}", response_model=CityOut)
@limit(READ_RATE)
async def get_city(
    city_id: int,
    request: Request,
    profile: CurrentProfile,
    db: DbSession,
):
    """One city by GeoNames id, including its coordinates.

    Needed by the map on a trip's detail page: the trip stores `city_id` but not
    a coordinate, and re-deriving the location from a *name* search would be
    wrong — `q=San Jose` returns whichever of the four namesakes ranks highest,
    which is frequently not the one the trip stored. Resolving by id is the only
    lookup that returns the same city every time.

    A missing id is a real **404**, unlike the empty list from the prefix search:
    the caller named a specific row, so "it is not there" is an error rather than
    an ordinary no-match. The id comes from a `city_id` that
    `resolve_city_id()` validated at write time, so a 404 here means the row was
    deleted afterwards (a GeoNames re-import can do this) and the FK was set to
    `NULL` — the client should fall back to the city name as plain text.
    """
    city = await db.scalar(select(City).where(City.id == city_id))
    if city is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown city_id.",
        )
    return CityOut.model_validate(city)


def _prefix_match(pattern: str):
    """Case-insensitive prefix match that keeps the index usable on both dialects.

    Wrapping the column in a function (`lower(asciiname) LIKE ?`) discards the
    index and turns the query into a full scan. Measured against the
    69,740-row table:

        asciiname LIKE ?          -> SEARCH cities USING INDEX (asciiname>? AND asciiname<?)
        lower(asciiname) LIKE ?   -> SCAN cities

    So the dialect decides how case-insensitivity is obtained:

    * **SQLite** — `LIKE` is already case-insensitive for ASCII *and* the column
      is declared `COLLATE NOCASE`, so a bare `LIKE` on the plain column matches
      the index. Adding `lower()` here would be the defect described above.
    * **PostgreSQL** — `LIKE` is case-**sensitive** and the column has no
      `NOCASE` collation, so the bare form would silently return nothing for a
      lowercase term. `ILIKE` is the correct primitive there. It does not use a
      plain B-tree index either, which is the documented reason a PostgreSQL
      deployment should add a `citext` column or a functional index on
      `lower(asciiname)` before this endpoint sees real traffic.

    The dialect is read from the configured URL rather than from the session:
    `session.bind` is a sync-engine accessor on an `AsyncSession`, and reading it
    inside a request is an unnecessary coupling when the answer is static for the
    lifetime of the process.
    """
    if settings.DATABASE_URL.startswith("postgresql"):
        return City.asciiname.ilike(pattern, escape="\\")
    return City.asciiname.like(pattern, escape="\\")
