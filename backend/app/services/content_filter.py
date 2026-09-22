"""Text moderation for user-generated content.

**What this is.** A speed bump for the obvious cases, not a safety mechanism. The
safety mechanism is the block/report flow plus a human reviewing reports. Anything
here that tries to be clever is a liability, because on a travel app the cost of a
false positive ("hotel *deposit*", "killer view", "Gunsan") is a real user unable
to publish, while the cost of a false negative is one more item in the report
queue. So the local rules are deliberately *high precision* and narrow, and the
ambiguous cases are flagged for review rather than blocked.

**Two layers.**

1. **Local rules** (`check_local`) — always on, offline, deterministic. These are
   the only thing that can *block*.
2. **External provider** — optional, selected by `CONTENT_FILTER_PROVIDER`. Like
   `services/sms.py`, it is one interface over an integration point: point the
   webhook at a thin adapter for your vendor rather than teaching this module
   every vendor's schema. A provider that errors or times out returns "no
   opinion" and the local verdict stands (fail-open, see below).

**Verdicts.** `ALLOW` / `FLAG` / `BLOCK`. Only `BLOCK` rejects a write. `FLAG`
lets the content through and records a counter — it exists so that the
"is this getting worse?" question has an answer without anyone having to guess.

**Fail-open.** If the provider cannot answer, content is allowed and the failure
is logged loudly. This mirrors the rate limiter's documented trade-off: a
moderation outage must not become a total write outage. The asymmetry is
deliberate — the block/report flow still works, so a user is never left without a
way to protect themselves.

**Evasion.** `_variants` folds full-width forms and neutralises zero-width
characters, which raises the effort from trivial to mild. It is not a boundary and
is not claimed to be: someone determined will get through, and that is fine,
because this is not what stops them.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

import httpx
from fastapi import HTTPException, status

from app.core import metrics
from app.core.config import settings

logger = logging.getLogger("tripmate.content")

#: Where the text came from. A closed set because it becomes a metric label —
#: an open set would let a caller create unbounded time series.
Field = Literal["trip", "profile", "review", "chat"]

_ACTIONS = ("ALLOW", "FLAG", "BLOCK")


@dataclass(frozen=True)
class Verdict:
    """Outcome of a moderation pass.

    `reason` is a stable code (e.g. `"off_platform_payment"`), never the matched
    text: it is written to logs and metric labels, and echoing the match back
    would hand an attacker the rule set one rejection at a time.
    """

    action: Literal["ALLOW", "FLAG", "BLOCK"]
    reason: str | None = None

    def __post_init__(self) -> None:
        assert self.action in _ACTIONS, self.action


ALLOW = Verdict("ALLOW")

# --- normalisation ---------------------------------------------------------

# Zero-width and bidi-control characters: invisible, so `pro\u200bstitute` reads
# as the banned word to a human but not to a regex.
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_WHITESPACE = re.compile(r"\s+")


# --- local rules -----------------------------------------------------------
#
# Every pattern is word-bounded and case-insensitive. Each entry is
# (reason code, verdict, pattern). Order matters only for which code wins.
#
# `BLOCK` is reserved for phrases with no legitimate reading in a travel-mate
# chat. Anything a real traveller could plausibly write is `FLAG` at most — the
# cost of getting that wrong is a blocked user, which is worse than a queue item.

_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    # --- blocked ----------------------------------------------------------
    (
        "sexual_solicitation",
        "BLOCK",
        re.compile(
            r"\b(?:prostitut\w*|escort\s+service|sex\s+for\s+(?:money|cash)"
            r"|happy\s+ending|full\s+service\s+massage)\b",
            re.I,
        ),
    ),
    (
        "violent_threat",
        "BLOCK",
        re.compile(
            # First person + intent + a person object. "This traffic will kill me"
            # and "killer view" do not match; "I will kill you" does.
            r"\b(?:i|we)(?:'|’)?(?:ll| will| am going to|'m going to| gonna)\s+"
            r"(?:kill|stab|shoot|beat|hurt|find)\s+(?:you|u|him|her|them|your\s+family)\b",
            re.I,
        ),
    ),
    (
        "off_platform_payment",
        "BLOCK",
        # Money-transfer rails. A legitimate trip organiser splitting costs does
        # not ask for a Western Union or MoneyGram transfer; these are the
        # textbook advance-fee markers.
        re.compile(r"\b(?:western\s+union|money\s*gram)\b", re.I),
    ),
    (
        "illegal_goods",
        "BLOCK",
        re.compile(
            r"\b(?:buy|sell|selling|order)\s+(?:me|you|us|him|her|them)?\s*(?:some\s+)?"
            r"(?:cocaine|meth|heroin|ecstasy|mdma|weed|marijuana)\b",
            re.I,
        ),
    ),
    # --- flagged ----------------------------------------------------------
    (
        "payment_solicitation",
        "FLAG",
        # "Deposit" alone is ordinary travel talk (hotel deposit, rental deposit),
        # so this requires an explicit request to send money to a person.
        re.compile(
            r"\b(?:transfer|send|wire|pay)\s+(?:me|us)\s+(?:the\s+)?"
            r"(?:money|cash|deposit|fee|hk\$|\$|usd|eur|gbp)\b",
            re.I,
        ),
    ),
    (
        "off_platform_contact",
        "FLAG",
        # Travellers legitimately swap handles, and the app has its own chat. Worth
        # a look in aggregate, never worth a rejection.
        re.compile(
            r"\b(?:whats\s?app|telegram|we\s?chat|line\s?id|signal|kik|snapchat)\b",
            re.I,
        ),
    ),
    (
        "link_heavy",
        "FLAG",
        re.compile(r"\b(?:bit\.ly|tinyurl|t\.co|goo\.gl|is\.gd|cutt\.ly)\b", re.I),
    ),
)


def _variants(text: str) -> tuple[str, ...]:
    """Normalised forms to match a rule against.

    There are **two**, because removing zero-width characters and replacing them
    with a space catch different evasions and neither is a superset of the other:

    * `pro\u200bstitute`     -- removing wins  -> `prostitute`
    * `escort\u200bservice`  -- spacing wins   -> `escort service`

    Stripping alone turns the second into `escortservice`, which matches nothing;
    spacing alone turns the first into `pro stitute`. Matching both costs one
    extra pass over a short string, which is far cheaper than either blind spot.

    NFKC additionally collapses full-width forms. The whitespace collapse only
    affects runs of 2+, so `k i l l` still reads as `k i l l` -- closing that
    would mean deleting *all* spaces and creating false positives across every
    word boundary, which is not a trade worth making.
    """
    folded = unicodedata.normalize("NFKC", text or "")
    joined = _WHITESPACE.sub(" ", _INVISIBLE.sub("", folded)).strip()
    spaced = _WHITESPACE.sub(" ", _INVISIBLE.sub(" ", folded)).strip()
    return (joined,) if joined == spaced else (joined, spaced)


def check_local(text: str) -> Verdict:
    """Apply the local rules. Pure and synchronous, so it is trivial to test."""
    if not text:
        return ALLOW
    haystacks = _variants(text)
    verdict = ALLOW
    for reason, action, pattern in _RULES:
        if any(pattern.search(haystack) for haystack in haystacks):
            if action == "BLOCK":
                # A block short-circuits: there is no reason to keep looking.
                return Verdict("BLOCK", reason)
            if verdict.action == "ALLOW":
                verdict = Verdict("FLAG", reason)
    return verdict


# --- external provider -----------------------------------------------------

_PROVIDER_VERDICTS = {"block": "BLOCK", "flag": "FLAG", "allow": "ALLOW"}


async def _review_webhook(text: str, field: str) -> Verdict | None:
    """Ask the external provider. Returns None when it cannot answer.

    `None` means "no opinion", not "clean" — the caller keeps the local verdict,
    so a provider outage can only ever *fail open*, never fail closed.
    """
    if not settings.CONTENT_WEBHOOK_URL:
        logger.warning(
            "content_filter: CONTENT_FILTER_PROVIDER=webhook but CONTENT_WEBHOOK_URL "
            "is empty — running on local rules only"
        )
        return None

    headers = {"Content-Type": "application/json"}
    if settings.CONTENT_WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {settings.CONTENT_WEBHOOK_TOKEN}"

    try:
        async with httpx.AsyncClient(
            timeout=settings.CONTENT_WEBHOOK_TIMEOUT_SECONDS
        ) as client:
            resp = await client.post(
                settings.CONTENT_WEBHOOK_URL,
                json={"text": text, "field": field},
                headers=headers,
            )
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{resp.status_code}", request=resp.request, response=resp
            )
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - any failure must degrade, not raise
        metrics.observe_content_provider_error()
        if not settings.CONTENT_FILTER_FAIL_OPEN:
            # Documented as off by default. Failing closed turns a vendor outage
            # into a total write outage, which is a much larger incident.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="內容審核服務暫時無法使用，請稍後再試。",
            ) from exc
        logger.warning(
            "content_moderation_provider_failed",
            extra={"field": field, "error": type(exc).__name__},
        )
        return None

    action = _PROVIDER_VERDICTS.get(str(payload.get("action", "")).lower())
    if action is None:
        # An unrecognised action is a broken integration, not a verdict. Treat it
        # as no opinion rather than silently guessing.
        logger.warning(
            "content_moderation_provider_unexpected_response",
            extra={"field": field, "action": payload.get("action")},
        )
        metrics.observe_content_provider_error()
        return None

    reason = payload.get("reason")
    return Verdict(action, str(reason)[:60] if reason else "external_provider")


# --- public API ------------------------------------------------------------

#: The client sees this and only this. Naming the rule would let a determined
#: user iterate against the filter; naming the *field* is safe and actually helps
#: a legitimate user find the box they need to edit.
_REJECTION_DETAIL = "這段內容含有不符合社群規範的資訊，請修改後再試。"

#: Field name -> the label a user would recognise from the form they just filled.
_FIELD_LABELS = {
    "title": "標題",
    "description": "行程說明",
    "nickname": "暱稱",
    "bio": "個人簡介",
    "comment": "評價內容",
    "content": "訊息",
    "summary": "足跡摘要",
}


async def moderate(text: str, *, field: Field) -> Verdict:
    """Full pipeline: local rules, then the provider if one is configured."""
    if not settings.CONTENT_FILTER_ENABLED or not text:
        return ALLOW

    local = check_local(text)
    if local.action == "BLOCK":
        # Already blocked; do not spend a network round-trip confirming it.
        return local

    if settings.CONTENT_FILTER_PROVIDER == "webhook":
        remote = await _review_webhook(text, field)
        if remote is not None and remote.action == "BLOCK":
            return remote

    return local


async def screen(
    text: str, *, field: Field, label: str | None = None
) -> tuple[Verdict, str | None]:
    """Moderate and record the outcome. Never raises.

    Returns `(verdict, rejection_detail)`; `rejection_detail` is non-None only for
    a `BLOCK`, and is the client-safe message.

    Split out from `enforce` so a non-HTTP caller can reject in its own idiom.
    The WebSocket path is the reason this exists: `HTTPException` has no meaning
    on a socket, so that caller needs the verdict and the message separately in
    order to send an error frame it can actually render.

    Rejections are counted and logged but deliberately **not** written to the
    audit trail: the trail is scoped to the four categories the Security Spec
    names (blocking, reporting, erasure, admin action), and a rejected write is a
    high-volume operational signal rather than an accountability record. This is
    the same reasoning that keeps login failures out of it — a spammer retrying
    must not be able to grow the one table that is never pruned.
    """
    verdict = await moderate(text, field=field)

    if verdict.action == "BLOCK":
        metrics.observe_content_blocked(field, verdict.reason or "unknown")
        logger.warning(
            "content_blocked",
            extra={
                "field": field,
                "reason": verdict.reason,
                # Length, not content: the log must not become a copy of the text
                # the filter just refused.
                "text_length": len(text),
            },
        )
        return verdict, f"{label}：{_REJECTION_DETAIL}" if label else _REJECTION_DETAIL

    if verdict.action == "FLAG":
        metrics.observe_content_flagged(field, verdict.reason or "unknown")
        logger.info(
            "content_flagged",
            extra={"field": field, "reason": verdict.reason, "text_length": len(text)},
        )

    return verdict, None


async def enforce(text: str, *, field: Field, label: str | None = None) -> Verdict:
    """Moderate `text` and raise 422 if it must be rejected."""
    verdict, detail = await screen(text, field=field, label=label)
    if detail is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail
        )
    return verdict


async def enforce_many(*, field: Field, **texts: str | None) -> None:
    """Enforce over several named fields, naming the offending one in the error.

    The field *label* is included because the user can act on it ("行程說明：…");
    the rule that matched still is not.
    """
    for name, value in texts.items():
        if not value:
            continue
        await enforce(value, field=field, label=_FIELD_LABELS.get(name, name))
