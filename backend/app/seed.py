"""Seed the database with demo users + trip posts.

Usage:  python -m app.seed
"""
import asyncio
from datetime import date, timedelta

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal, init_db
from app.models.enums import BudgetType, Gender, TargetGender
from app.models.profile import Profile, TravelHistory
from app.models.trip import TripPost
from app.models.user import User

DEMO_PASSWORD = "Passw0rd123"

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
            ("Japan", "Tokyo", 2024, BudgetType.MODERATE, "追櫻之旅，拍了很多照片。"),
            ("Taiwan", "Taipei", 2023, BudgetType.BUDGET, "夜市與咖啡店巡禮。"),
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
            ("Nepal", "Pokhara", 2023, BudgetType.BUDGET, "ABC 健行，風景壯麗。"),
            ("Vietnam", "Hanoi", 2024, BudgetType.BUDGET, "下龍灣與街頭美食。"),
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
            ("Thailand", "Bangkok", 2024, BudgetType.MODERATE, "街頭小吃天堂。"),
            ("Taiwan", "Taipei", 2022, BudgetType.MODERATE, "夜市全制霸。"),
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
        "budget_type": BudgetType.MODERATE,
        "target_gender": TargetGender.ANY,
        "tags": ["PHOTOGRAPHY", "NATURE"],
        "days_from_now": 40,
    },
    {
        "author": "+85290000002",
        "title": "尼泊爾 ABC 健行 12 天",
        "description": "Annapurna Base Camp 路線，中等難度，需要能走 6 小時以上的夥伴。",
        "destination_country": "Nepal",
        "destination_city": "Pokhara",
        "budget_type": BudgetType.BUDGET,
        "target_gender": TargetGender.ANY,
        "tags": ["HIKING", "ADVENTURE"],
        "days_from_now": 75,
    },
    {
        "author": "+85290000003",
        "title": "台北夜市美食團",
        "description": "三日兩夜，重點是士林、饒河、寧夏夜市。喜歡吃的人一起來！",
        "destination_country": "Taiwan",
        "destination_city": "Taipei",
        "budget_type": BudgetType.MODERATE,
        "target_gender": TargetGender.ANY,
        "tags": ["FOOD", "CULTURE"],
        "days_from_now": 20,
    },
]


async def seed() -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        created: dict[str, Profile] = {}

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

            for country, city, year, budget, summary in spec["history"]:
                db.add(
                    TravelHistory(
                        profile_id=profile.id,
                        country=country,
                        city=city,
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
                    start_date=start,
                    end_date=start + timedelta(days=7),
                    budget_type=trip["budget_type"],
                    target_gender=trip["target_gender"],
                    tags=trip["tags"],
                    looking_for_count=2,
                )
            )

        await db.commit()

    print("Seed complete. Demo login: +85290000001 / Passw0rd123")


if __name__ == "__main__":
    asyncio.run(seed())
