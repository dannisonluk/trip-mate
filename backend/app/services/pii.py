"""PII masking utilities (§2.2).

These helpers are the ONLY sanctioned way to surface contact details. Raw
values must never be serialized to a client that is not the owner.
"""
from __future__ import annotations

import re

_PHONE_DIGITS = re.compile(r"\D")


def mask_phone(raw: str | None) -> str | None:
    """Mask a phone number for display, e.g. '+852 9123 4123' -> '+852 9*** *123'.

    Keeps the country code and the last 3 digits; everything in between becomes '*'.
    """
    if not raw:
        return None

    prefix = ""
    body = raw
    if raw.strip().startswith("+"):
        m = re.match(r"^\+(\d{1,3})\s*(.*)$", raw.strip())
        if m:
            prefix, body = f"+{m.group(1)} ", m.group(2)

    digits = _PHONE_DIGITS.sub("", body)
    if len(digits) <= 4:
        return f"{prefix}{'*' * len(digits)}"

    head, tail = digits[0], digits[-3:]
    masked = f"{head}*** *{tail}"
    return f"{prefix}{masked}"


def mask_email(raw: str | None) -> str | None:
    """Mask an email, e.g. 'alice@example.com' -> 'a***e@example.com'."""
    if not raw or "@" not in raw:
        return None
    local, _, domain = raw.partition("@")
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = f"{local[0]}***{local[-1]}"
    return f"{masked_local}@{domain}"


def is_phone_like(text: str) -> bool:
    """Heuristic used to warn users before they leak a number in chat."""
    digits = _PHONE_DIGITS.sub("", text)
    return len(digits) >= 8
