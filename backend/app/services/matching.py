"""Trip-companion matching / recommendation engine (async).

A lightweight, explainable scorer — no ML dependency required. Signals used:

  * destination overlap with the profile's travel history
  * shared travel-style tags between profile and post
  * language overlap with the post creator
  * budget alignment with what the user usually spends
  * freshness and upcoming departure

Every recommendation returns human-readable `reasons` so the UI can explain
"why this match" — trust matters more than an opaque score in this domain.
"""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import Profile, TravelHistory
from app.models.trip import TripPost
from app.services.moderation import blocked_profile_ids


@dataclass
class Recommendation:
    post: TripPost
    score: float
    reasons: list[str] = field(default_factory=list)


def _normalise(values) -> set[str]:
    return {str(v).lower() for v in (values or []) if v}


async def _profile_signals(db: AsyncSession, profile: Profile) -> dict:
    history = (
        await db.execute(select(TravelHistory).where(TravelHistory.profile_id == profile.id))
    ).scalars().all()

    countries = _normalise(h.country for h in history)
    cities = _normalise(h.city for h in history)
    budgets = [h.budget_type.value for h in history if h.budget_type]
    favourite_budget = max(set(budgets), key=budgets.count) if budgets else None

    return {
        "countries": countries,
        "cities": cities,
        "tags": _normalise(profile.travel_style_tags),
        "languages": _normalise(profile.languages),
        "favourite_budget": favourite_budget,
    }


async def recommend_trips(
    db: AsyncSession, profile: Profile, *, limit: int = 20, exclude_own: bool = True
) -> list[Recommendation]:
    signals = await _profile_signals(db, profile)
    hidden = await blocked_profile_ids(db, profile.id)

    stmt = (
        select(TripPost)
        .where(TripPost.status == "OPEN")
        .order_by(TripPost.created_at.desc())
        .limit(200)
    )
    posts = (await db.execute(stmt)).scalars().all()

    today = date.today()
    results: list[Recommendation] = []

    for post in posts:
        if exclude_own and post.creator_id == profile.id:
            continue
        if post.creator_id in hidden:
            continue

        score = 0.0
        reasons: list[str] = []

        country = (post.destination_country or "").lower()
        city = (post.destination_city or "").lower()

        # Destination affinity — the strongest signal.
        if city and city in signals["cities"]:
            score += 3.0
            reasons.append(f"你曾到訪 {post.destination_city}")
        elif country and country in signals["countries"]:
            score += 2.0
            reasons.append(f"你熟悉 {post.destination_country}")

        # Shared travel-style tags.
        overlap = _normalise(post.tags) & signals["tags"]
        if overlap:
            score += 1.5 * len(overlap)
            reasons.append("共同旅遊風格：" + "、".join(sorted(overlap)))

        # Language overlap with the creator.
        creator = post.creator
        if creator and creator.languages:
            shared_lang = _normalise(creator.languages) & signals["languages"]
            if shared_lang:
                score += 1.0
                reasons.append("共同語言：" + "、".join(sorted(shared_lang)))

        # Budget alignment.
        if signals["favourite_budget"] and post.budget_type.value == signals["favourite_budget"]:
            score += 0.8
            reasons.append("預算習慣相近")

        # Recency.
        if post.created_at:
            age_days = (today - post.created_at.date()).days
            if age_days <= 3:
                score += 1.0
                reasons.append("近期發佈")
            elif age_days <= 14:
                score += 0.4

        # Upcoming departure.
        if post.start_date and post.start_date >= today:
            score += 0.6
            reasons.append("即將出發")

        if score > 0:
            results.append(Recommendation(post=post, score=round(score, 2), reasons=reasons))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:limit]
