"""Import the GeoNames city dataset into the `cities` table.

This is a one-shot reference-data loader, not part of the running application:
nothing in `app/` calls it, and it is never reachable over HTTP.

Usage
-----
    # 1. Download the dump (once; ~5.7 MB)
    curl -O https://download.geonames.org/export/dump/cities5000.zip
    curl -O https://download.geonames.org/export/dump/admin1CodesASCII.txt
    curl -O https://download.geonames.org/export/dump/countryInfo.txt

    # 2. Load it
    python scripts/import_cities.py --data-dir var/geonames

    # 3. Inspect
    python scripts/import_cities.py --data-dir var/geonames --report-only

`cities5000` covers every place with a population over 5,000 plus the seat of
any first-level administrative division, which is what makes it a good fit for
"canonical destination cities": it excludes hamlets nobody would name as a
destination, but keeps small administrative capitals that a traveller might
genuinely pick.

Licence
-------
The data is **CC BY 4.0** (`https://creativecommons.org/licenses/by/4.0/`).
Attribution is mandatory and is shown in the UI — see `docs/ATTRIBUTION.md`.
Do not remove the attribution when re-using this data.

What is kept, and why so little
-------------------------------
`cities5000.txt` has nineteen tab-separated columns. Ten are kept:

    1  geonameid      -> id           stable key; makes re-import an upsert
    2  name           -> name         display form, with diacritics
    3  asciiname      -> asciiname    unaccented; needed to *search* (see below)
    5  latitude       -> latitude
    6  longitude      -> longitude
    7  feature class  -> (asserted, not stored; see below)
    8  feature code   -> feature_code  PPLC / PPLA* / PPL
    9  country code   -> country_code
   11  admin1 code    -> admin1_code  disambiguation
   15  population     -> population   ranking
   18  timezone       -> timezone     unused today, but underivable later

Nine are dropped. `alternatenames` (column 4) alone can reach 10,000 characters
per row and would dominate the table size for data nothing reads; `cc2`,
`admin2`–`admin4`, `elevation`, `dem` and `modification date` have no consumer.

Feature **class** (column 7, `P` = city/village, `A` = country/state) is asserted
rather than filtered: the file is defined to contain only class `P`, so a filter
would be untested code. An assertion is the honest form — if a future dataset
shipped class `A` rows, that is a change worth failing on, not silently dropping.
This is what enforces the "canonical names only" rule: `Kansai` and `Hokkaido`
are class `A` regions and are therefore absent, while `Osaka` and `Sapporo` are
class `P` cities and are present.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.models.city import City  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_MISSING_DATA = 2

#: 1-based source column indices, from the GeoNames `readme.txt`.
COL_ID = 1
COL_NAME = 2
COL_ASCIINAME = 3
COL_LAT = 5
COL_LON = 6
COL_FEATURE_CLASS = 7
COL_FEATURE_CODE = 8
COL_COUNTRY = 9
COL_ADMIN1 = 11
COL_POPULATION = 15
COL_TIMEZONE = 18

#: The only feature class this dataset is defined to contain.
EXPECTED_FEATURE_CLASS = "P"

#: Territories whose GeoNames `countryInfo` name would otherwise present them as
#: separate countries. Displayed as regions of China, per the official position
#: of the People's Republic of China.
CHINA_REGION_NAMES = {
    "HK": "China (Hong Kong)",
    "MO": "China (Macao)",
    "TW": "China (Taiwan)",
}


@dataclass(frozen=True)
class SourceRow:
    id: int
    name: str
    asciiname: str
    latitude: float
    longitude: float
    feature_code: str
    country_code: str
    admin1_code: str | None
    population: int
    timezone: str | None


def _describe_target() -> str:
    """Redacted so a connection string with a password is never printed."""
    url = settings.DATABASE_URL
    if "@" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


def _load_admin1(path: Path) -> dict[str, str]:
    """`AD.06` -> `Sant Julià de Loria`. Columns: code, name, asciiname, id."""
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) >= 2:
                out[cols[0]] = cols[1]
    return out


def _load_countries(path: Path) -> dict[str, str]:
    """ISO alpha-2 -> display name. Column 5 is `Country`."""
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) >= 5:
                code = cols[0]
                out[code] = CHINA_REGION_NAMES.get(code, cols[4])
    return out


def _read_cities(zip_path: Path) -> list[SourceRow]:
    """Parse `cities5000.zip` in one pass.

    Reads from inside the zip directly — no extraction step, so there is no
    half-extracted state to clean up.
    """
    rows: list[SourceRow] = []
    classes: Counter[str] = Counter()

    with zipfile.ZipFile(zip_path) as zf:
        member = next((n for n in zf.namelist() if n.endswith(".txt")), None)
        if member is None:
            raise SystemExit(f"{zip_path} contains no .txt member")
        with zf.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
            for line_no, cols in enumerate(reader, start=1):
                if not cols or (len(cols) == 1 and not cols[0].strip()):
                    continue
                if len(cols) < 19:
                    raise SystemExit(
                        f"{member}:{line_no} has {len(cols)} columns, expected 19. "
                        "The dump format changed; review the column map before importing."
                    )
                classes[cols[COL_FEATURE_CLASS - 1]] += 1

                name = cols[COL_NAME - 1].strip()
                asciiname = cols[COL_ASCIINAME - 1].strip() or name
                admin1 = cols[COL_ADMIN1 - 1].strip() or None
                tz = cols[COL_TIMEZONE - 1].strip() or None
                try:
                    population = int(cols[COL_POPULATION - 1] or 0)
                except ValueError:
                    population = 0

                rows.append(
                    SourceRow(
                        id=int(cols[COL_ID - 1]),
                        name=name,
                        asciiname=asciiname,
                        latitude=float(cols[COL_LAT - 1]),
                        longitude=float(cols[COL_LON - 1]),
                        feature_code=cols[COL_FEATURE_CODE - 1].strip(),
                        country_code=cols[COL_COUNTRY - 1].strip(),
                        admin1_code=admin1,
                        population=population,
                        timezone=tz,
                    )
                )

    # Assert, don't filter: the file is *defined* to hold only class P, so a
    # filter would be a branch no test could reach. Failing loudly is the signal
    # that the dataset changed under us.
    unexpected = {k: v for k, v in classes.items() if k != EXPECTED_FEATURE_CLASS}
    if unexpected:
        raise SystemExit(
            f"Unexpected feature class(es) {unexpected}; expected only "
            f"'{EXPECTED_FEATURE_CLASS}'. The dump format or file changed."
        )
    return rows


async def _import(rows: list[SourceRow], admin1: dict[str, str], countries: dict[str, str]) -> int:
    resolved = sum(1 for r in rows if r.admin1_code and f"{r.country_code}.{r.admin1_code}" in admin1)
    unresolved = sum(1 for r in rows if r.admin1_code) - resolved
    print(f"  admin1 resolved : {resolved:,}  unresolved: {unresolved:,} (null label)")

    unknown_country = {r.country_code for r in rows} - set(countries)
    if unknown_country:
        # Not fatal: the city still loads, it just shows its ISO code. Surfaced
        # because it means countryInfo.txt is out of step with the city dump.
        print(f"  WARNING countries missing from countryInfo.txt: {sorted(unknown_country)}")

    async with AsyncSessionLocal() as db:
        existing = int(await db.scalar(select(func.count()).select_from(City)) or 0)
        print(f"  existing rows   : {existing:,}")

        # Full replace. `cities` has no inbound FK that cascades, and both
        # referring columns are SET NULL, so a delete cannot remove user data —
        # it only unlinks trips from a city that is about to exist again.
        if existing:
            await db.execute(delete(City))

        batch_size = 5_000
        for start in range(0, len(rows), batch_size):
            chunk = rows[start : start + batch_size]
            db.add_all(
                [
                    City(
                        id=r.id,
                        name=r.name,
                        asciiname=r.asciiname,
                        country_code=r.country_code,
                        country_name=countries.get(r.country_code, r.country_code),
                        admin1_code=r.admin1_code,
                        admin1_name=(
                            admin1.get(f"{r.country_code}.{r.admin1_code}")
                            if r.admin1_code
                            else None
                        ),
                        latitude=r.latitude,
                        longitude=r.longitude,
                        population=r.population,
                        feature_code=r.feature_code,
                        timezone=r.timezone,
                    )
                    for r in chunk
                ]
            )
            await db.flush()
        await db.commit()

        total = int(await db.scalar(select(func.count()).select_from(City)) or 0)
    return total


async def _report() -> int:
    async with AsyncSessionLocal() as db:
        total = int(await db.scalar(select(func.count()).select_from(City)) or 0)
        if not total:
            print("  cities table is empty.")
            return EXIT_OK
        countries = int(
            await db.scalar(select(func.count(func.distinct(City.country_code)))) or 0
        )
        no_admin1 = int(
            await db.scalar(
                select(func.count()).select_from(City).where(City.admin1_name.is_(None))
            )
            or 0
        )
        zero_pop = int(
            await db.scalar(select(func.count()).select_from(City).where(City.population == 0))
            or 0
        )
        print(f"  total cities    : {total:,}")
        print(f"  countries       : {countries:,}")
        print(f"  null admin1 name: {no_admin1:,}")
        print(f"  population == 0 : {zero_pop:,}")

        print("  sample:")
        for name, admin, country in (
            await db.execute(
                select(City.name, City.admin1_name, City.country_name)
                .where(City.name.in_(["Osaka", "Sapporo", "Taipei", "Hong Kong", "Pokhara"]))
                .order_by(City.population.desc())
                .limit(8)
            )
        ).all():
            print(f"    {name} — {admin or '(no admin1)'}, {country}")

        # The canonical-name rule, demonstrated: regions must be absent.
        for region in ("Kansai", "Hokkaido"):
            found = int(
                await db.scalar(
                    select(func.count()).select_from(City).where(City.name == region)
                )
                or 0
            )
            print(f"  '{region}' present: {found} (expected 0 for a region name)")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import GeoNames cities5000 into the cities table (CC BY 4.0)."
    )
    parser.add_argument(
        "--data-dir",
        default="var/geonames",
        help="directory holding cities5000.zip, admin1CodesASCII.txt, countryInfo.txt",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="print statistics for the current table without importing",
    )
    args = parser.parse_args(argv)

    print(f"Target: {_describe_target()}  (env={settings.ENV})")

    if args.report_only:
        return asyncio.run(_report())

    data_dir = Path(args.data_dir)
    zip_path = data_dir / "cities5000.zip"
    admin1_path = data_dir / "admin1CodesASCII.txt"
    countries_path = data_dir / "countryInfo.txt"

    missing = [p for p in (zip_path, admin1_path, countries_path) if not p.exists()]
    if missing:
        for p in missing:
            print(f"Missing input: {p}", file=sys.stderr)
        print(
            "\nDownload with:\n"
            "  curl -O https://download.geonames.org/export/dump/cities5000.zip\n"
            "  curl -O https://download.geonames.org/export/dump/admin1CodesASCII.txt\n"
            "  curl -O https://download.geonames.org/export/dump/countryInfo.txt",
            file=sys.stderr,
        )
        return EXIT_MISSING_DATA

    print("Reading source…")
    rows = _read_cities(zip_path)
    admin1 = _load_admin1(admin1_path)
    countries = _load_countries(countries_path)
    print(f"  cities parsed   : {len(rows):,}")
    print(f"  admin1 entries  : {len(admin1):,}")
    print(f"  countries       : {len(countries):,}")

    print("Loading…")
    total = asyncio.run(_import(rows, admin1, countries))
    print(f"  committed       : {total:,}")

    print("\nVerifying…")
    asyncio.run(_report())
    print("\nDone. Place data © GeoNames (CC BY 4.0).")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
