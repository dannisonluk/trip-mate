"""The recency bucket must be computed in UTC (audit finding B9).

The defect being guarded
------------------------
`recommend_trips` used `date.today()`, which reads the **server's local
timezone**, and compared it against `TripPost.created_at.date()`, which is
stored in **UTC**. On a host east of UTC the subtraction under-counts the age
(a trip posted today can look "0 days old" when it is 1), and on a host west of
UTC it over-counts — a trip posted five minutes ago can come out as *yesterday*,
or, near midnight, as several days old. Either way the same request produced a
different score depending on which replica answered.

The consequence is not cosmetic: `age_days <= 3` is worth +1.0, the largest
single non-destination weight in the function. A freshly-posted trip silently
losing it drops out of the "recommended" ordering the user is shown.

How this is tested without a second timezone
--------------------------------------------
The process timezone is not something a test can portably move. So the fix has
two halves, each tested separately:

* the **scoring rule** is now a pure function (`matching.score_post`) that takes
  `today` as an argument — so the bucket boundary can be asserted directly
  against a UTC date, with no clock involved;
* the **caller** is asserted to pass a UTC date and nothing else. That is the
  half a pure-function test cannot see, and the half the bug actually lived in.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

from app.models.trip import TripPost
from app.services import matching

_MATCHING_SRC = Path(matching.__file__)


def _empty_signals() -> dict:
    """No affinity at all, so only recency can move the score.

    Isolating recency is what makes these assertions about the *clock* rather
    than about destination matching: if any other weight fired, a positive score
    would no longer be evidence that the recency bucket worked.
    """
    return {
        "countries": set(),
        "cities": set(),
        "tags": set(),
        "languages": set(),
        "favourite_budget": None,
    }


def _post(created_at: datetime | None, start_date: date | None = None) -> TripPost:
    post = TripPost(
        title="Somewhere",
        destination_country="Japan",
        destination_city="Tokyo",
        budget_type="MODERATE",
        status="OPEN",
    )
    post.created_at = created_at
    post.start_date = start_date
    # `creator` stays None: no language signal, and no lazy-load in a unit test.
    post.creator = None
    return post


# ---------------------------------------------------------------------------
# The scoring rule (pure)
# ---------------------------------------------------------------------------
def test_a_post_created_today_earns_the_recency_bonus():
    today = date(2026, 9, 26)
    score, reasons = matching.score_post(
        _post(datetime(2026, 9, 26, 0, 5, tzinfo=timezone.utc)), _empty_signals(), today
    )
    assert "近期發佈" in reasons
    assert score == 1.0


def test_a_post_three_days_old_still_earns_the_bonus():
    """The boundary is inclusive — `<= 3`, not `< 3`."""
    today = date(2026, 9, 26)
    score, reasons = matching.score_post(
        _post(datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)), _empty_signals(), today
    )
    assert "近期發佈" in reasons
    assert score == 1.0


def test_a_post_four_days_old_drops_to_the_smaller_bonus():
    """Leaving the 3-day tier must fall to +0.4, not to nothing.

    The tiers are `<=3 -> 1.0`, `<=14 -> 0.4`, `else -> 0`. Asserting the
    *fallback* matters: a one-sided test ("the bonus is gone") would still pass
    if the whole second tier had been deleted.
    """
    today = date(2026, 9, 26)
    score, reasons = matching.score_post(
        _post(datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)), _empty_signals(), today
    )
    assert "近期發佈" not in reasons, "a 4-day-old post took the <=3 day bonus"
    assert score == 0.4, "a 4-day-old post should fall into the <=14 day tier"


def test_a_post_inside_the_fortnight_window_earns_the_smaller_bonus():
    today = date(2026, 9, 26)
    score, reasons = matching.score_post(
        _post(datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)), _empty_signals(), today
    )
    assert "近期發佈" not in reasons
    assert score == 0.4


def test_a_post_just_past_the_fortnight_window_earns_nothing():
    today = date(2026, 9, 26)
    score, _ = matching.score_post(
        _post(datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)), _empty_signals(), today
    )
    assert score == 0.0, "a 15-day-old post still scored — the tiers leaked"


def test_a_post_with_no_created_at_is_not_scored_on_recency():
    """Null must be skipped, not treated as epoch-zero (which is ancient)."""
    score, reasons = matching.score_post(_post(None), _empty_signals(), date(2026, 9, 26))
    assert "近期發佈" not in reasons
    assert score == 0.0


def test_an_upcoming_departure_uses_the_same_day_value():
    """The other `today` consumer must move with the same fix."""
    today = date(2026, 9, 26)
    score, reasons = matching.score_post(
        _post(None, start_date=date(2026, 10, 1)), _empty_signals(), today
    )
    assert "即將出發" in reasons
    assert score == 0.6

    score_past, reasons_past = matching.score_post(
        _post(None, start_date=date(2026, 9, 1)), _empty_signals(), today
    )
    assert "即將出發" not in reasons_past
    assert score_past == 0.0


# ---------------------------------------------------------------------------
# The caller — the half the pure test cannot see
# ---------------------------------------------------------------------------
def test_the_caller_passes_a_utc_date():
    """`date.today()` must not appear; a UTC-aware call must.

    `score_post` will happily accept any date it is handed, including a local
    one — so the pure tests above pass even if the caller is wrong. This is the
    assertion that actually pins B9: the value fed into the bucket has to come
    from a UTC clock.
    """
    source = _MATCHING_SRC.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert not re.search(r"\bdate\.today\(\)", code), (
        "matching.py still calls date.today() — that reads the server's local "
        "timezone while created_at is UTC, so scoring varies per replica"
    )
    assert re.search(r"datetime\.now\(timezone\.utc\)\.date\(\)", code), (
        "the recency bucket is not anchored to a UTC timestamp"
    )
