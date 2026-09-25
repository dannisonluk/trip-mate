"""Seed the database with demo users + trip posts.

Usage:  python -m app.seed
"""
import asyncio
from datetime import date, timedelta

from sqlalchemy import select, text

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal, init_db
from app.models.city import City
from app.models.enums import BudgetType, Gender, TargetGender
from app.models.profile import Profile, TravelHistory
from app.models.trip import TripPost
from app.models.user import User

DEMO_PASSWORD = "Passw0rd123"

#: PostgreSQL advisory-lock key serialising the seed across replicas (#22b).
#: The value is arbitrary; only its stability across processes matters. Chosen
#: from a hash of "tripmate-seed" so it does not collide with other advisory
#: locks in the same database by accident.
_SEED_LOCK_KEY = 0x745F5F7A  # "t__z"

#: A handful of real GeoNames rows, so `python -m app.seed` produces a working
#: city picker without requiring the 5.7 MB `cities5000` download first.
#:
#: These are **not** a substitute for `scripts/import_cities.py` — production
#: gets its ~69,740 rows from there. This is the minimum that makes the dev
#: environment and the E2E suite usable, and every value is copied from the real
#: dump so the demo data cannot diverge from what an import would produce.
#: Ids are GeoNames `geonameid`s. The histories and trips below reference these
#: by id — a demo row pointing at a `city_id` that does not exist would look
#: populated while silently never matching, which is exactly the failure mode
#: `resolve_city_id()` exists to prevent.
DEMO_CITIES = [
    # id, name, asciiname, cc, country, admin1, lat, lon, population, feature_code
    (1850147, "Tokyo", "Tokyo", "JP", "Japan", "Tokyo", 35.6895, 139.69171, 9733276, "PPLC"),
    (1853909, "Ōsaka", "Osaka", "JP", "Japan", "Osaka", 34.69379, 135.50107, 2753862, "PPLA"),
    (1668341, "Taipei", "Taipei", "TW", "China (Taiwan)", "Taipei", 25.05306, 121.52639, 7871900, "PPLC"),
    (1835848, "Seoul", "Seoul", "KR", "South Korea", "Seoul", 37.566, 126.9784, 10349312, "PPLC"),
    (1609350, "Bangkok", "Bangkok", "TH", "Thailand", "Bangkok", 13.75398, 100.50144, 5104476, "PPLC"),
    (1880252, "Singapore", "Singapore", "SG", "Singapore", None, 1.28967, 103.85007, 5638700, "PPLC"),
]

DEMO_USERS = [
    {
        "phone": "+85290000001",
        "nickname": "Alice 陳",
        "bio": "攝影愛好者，喜歡慢遊與清晨的光線。",
        "mbti": "INFP",
        "gender": Gender.FEMALE,
        "tags": ["PHOTOGRAPHY", "CAFE", "NATURE"],
        "languages": ["CANTONESE", "ENGLISH", "MANDARIN"],
        "history": [
            ("Japan", "Tokyo", 1850147, 2024, BudgetType.MODERATE, "追櫻之旅，拍了很多照片。"),
            ("Taiwan", "Taipei", 1668341, 2023, BudgetType.BUDGET, "夜市與咖啡店巡禮。"),
        ],
    },
    {
        "phone": "+85290000002",
        "nickname": "Bob 李",
        "bio": "背包客，去過 20 個國家，最愛登山。",
        "mbti": "ESTP",
        "gender": Gender.MALE,
        "tags": ["HIKING", "ADVENTURE", "BACKPACKER"],
        "languages": ["CANTONESE", "ENGLISH"],
        "history": [
            ("Japan", "Osaka", 1853909, 2023, BudgetType.BUDGET, "關西深度遊，去了很多寺廟。"),
            ("South Korea", "Seoul", 1835848, 2024, BudgetType.BUDGET, "首爾近郊行山。"),
        ],
    },
    {
        "phone": "+85290000003",
        "nickname": "Carla Wong",
        "bio": "Foodie exploring Asia one night market at a time.",
        "mbti": "ENFJ",
        "gender": Gender.FEMALE,
        "tags": ["FOOD", "CULTURE", "NIGHTLIFE"],
        "languages": ["ENGLISH", "CANTONESE"],
        "history": [
            ("Thailand", "Bangkok", 1609350, 2024, BudgetType.MODERATE, "街頭小吃天堂。"),
            ("Singapore", "Singapore", 1880252, 2022, BudgetType.MODERATE, "熟食中心全制霸。"),
        ],
    },
]

DEMO_TRIPS = [
    {
        "author": "+85290000001",
        "title": "東京櫻花季攝影之旅",
        "description": "計劃四月去東京拍櫻花，想找一位同樣喜歡攝影的旅伴，一起早起追光。",
        "destination_country": "Japan",
        "destination_city": "Tokyo",
        "city_id": 1850147,
        "budget_type": BudgetType.MODERATE,
        "target_gender": TargetGender.ANY,
        "tags": ["PHOTOGRAPHY", "NATURE"],
        "days_from_now": 40,
    },
    {
        "author": "+85290000002",
        "title": "首爾近郊行山 5 天",
        "description": "北漢山與首爾周邊路線，中等難度，需要能走 5 小時以上的夥伴。",
        "destination_country": "South Korea",
        "destination_city": "Seoul",
        "city_id": 1835848,
        "budget_type": BudgetType.BUDGET,
        "target_gender": TargetGender.ANY,
        "tags": ["HIKING", "ADVENTURE"],
        "days_from_now": 75,
    },
    {
        "author": "+85290000003",
        "title": "台北夜市美食團",
        "description": "三日兩夜，重點是士林、饒河、寧夏夜市。喜歡吃的人一起來！",
        "destination_country": "China (Taiwan)",
        "destination_city": "Taipei",
        "city_id": 1668341,
        "budget_type": BudgetType.MODERATE,
        "target_gender": TargetGender.ANY,
        "tags": ["FOOD", "CULTURE"],
        "days_from_now": 20,
    },
]


async def _seed_cities(db) -> None:
    """Insert the `DEMO_CITIES` reference rows, skipping any that already exist.

    Idempotent on purpose: `seed()` is re-run against an existing database in
    development, and re-importing GeoNames via `scripts/import_cities.py` will
    later insert the *same* primary keys. An unconditional `INSERT` would fail
    with an `IntegrityError` on the second run.
    """
    existing_ids = set(
        (await db.execute(select(City.id).where(City.id.in_([row[0] for row in DEMO_CITIES]))))
        .scalars()
        .all()
    )
    added = 0
    for (
        city_id,
        name,
        asciiname,
        country_code,
        country_name,
        admin1_name,
        latitude,
        longitude,
        population,
        feature_code,
    ) in DEMO_CITIES:
        if city_id in existing_ids:
            continue
        db.add(
            City(
                id=city_id,
                name=name,
                asciiname=asciiname,
                country_code=country_code,
                country_name=country_name,
                admin1_name=admin1_name,
                latitude=latitude,
                longitude=longitude,
                population=population,
                feature_code=feature_code,
            )
        )
        added += 1
    if added:
        await db.flush()


async def seed() -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        # --- Advisory lock (#22b) ------------------------------------------
        # Seeding is a *one-shot init job*, but nothing enforced that: on a
        # multi-replica rollout every replica that starts sees "the demo data is
        # missing" at the same moment and inserts it. The per-row existence
        # checks below do not help, because they run before any row is committed —
        # all N replicas read "absent", then all N insert. Depending on the table
        # that is either duplicate demo rows or an IntegrityError crash loop on
        # the unique phone constraint.
        #
        # A PostgreSQL advisory lock makes the whole seed mutually exclusive, so
        # the losers wait and then find every row already present (the existence
        # checks make the second pass a no-op). SQLite has no cross-process
        # advisory lock; it is a dev/test dialect where the seed is run by hand,
        # so the branch is an honest no-op rather than a fake guarantee.
        lock_held = False
        if settings.DATABASE_URL.startswith("postgresql"):
            # A fixed, arbitrary key: any stable constant works, it only has to
            # be the same on every replica.
            await db.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _SEED_LOCK_KEY})
            lock_held = True

        try:
            created: dict[str, Profile] = {}
            # Reference rows first: the histories below reference them, and with
            # `PRAGMA foreign_keys=ON` a missing row is a hard failure rather than a
            # silently absent link.
            await _seed_cities(db)

            for spec in DEMO_USERS:
                existing = (
                    await db.execute(select(User).where(User.phone_number == spec["phone"]))
                ).scalar_one_or_none()
                if existing:
                    created[spec["phone"]] = existing.profile
                    continue

                user = User(
                    phone_number=spec["phone"],
                    password_hash=hash_password(DEMO_PASSWORD),
                    is_verified=True,
                    consent_privacy=True,
                    consent_terms=True,
                )
                db.add(user)
                await db.flush()

                profile = Profile(
                    user_id=user.id,
                    nickname=spec["nickname"],
                    bio=spec["bio"],
                    mbti=spec["mbti"],
                    gender=spec["gender"],
                    travel_style_tags=spec["tags"],
                    languages=spec["languages"],
                )
                db.add(profile)
                await db.flush()

                for country, city, city_id, year, budget, summary in spec["history"]:
                    db.add(
                        TravelHistory(
                            profile_id=profile.id,
                            country=country,
                            city=city,
                            city_id=city_id,
                            start_date=date(year, 3, 1),
                            end_date=date(year, 3, 8),
                            budget_type=budget,
                            summary=summary,
                        )
                    )
                created[spec["phone"]] = profile

            for trip in DEMO_TRIPS:
                author = created.get(trip["author"])
                if author is None:
                    continue
                exists = (
                    await db.execute(
                        select(TripPost).where(
                            TripPost.creator_id == author.id, TripPost.title == trip["title"]
                        )
                    )
                ).scalar_one_or_none()
                if exists:
                    continue

                start = date.today() + timedelta(days=trip["days_from_now"])
                db.add(
                    TripPost(
                        creator_id=author.id,
                        title=trip["title"],
                        description=trip["description"],
                        destination_country=trip["destination_country"],
                        destination_city=trip["destination_city"],
                        city_id=trip["city_id"],
                        start_date=start,
                        end_date=start + timedelta(days=7),
                        budget_type=trip["budget_type"],
                        target_gender=trip["target_gender"],
                        tags=trip["tags"],
                        looking_for_count=2,
                    )
                )

            await db.commit()
        finally:
            if lock_held:
                # Release explicitly rather than relying on the session ending:
                # `pg_advisory_lock` is session-scoped, and a pooled connection
                # would otherwise carry the lock into its next user.
                await db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _SEED_LOCK_KEY})

    print("Seed complete. Demo login: +85290000001 / Passw0rd123")


if __name__ == "__main__":
    asyncio.run(seed())
