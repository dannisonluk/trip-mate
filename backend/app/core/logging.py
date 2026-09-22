"""Structured logging with request correlation.

Two goals, both security-relevant rather than cosmetic:

1. **One machine-readable line per event in production.** Free-text logs cannot
   answer "which requests failed for this account in the last hour" without grep
   gymnastics; a JSON object per record can. Development keeps a readable text
   format, because nobody debugs a stack trace in JSON by choice.

2. **Every record carries the request id.** A line emitted deep inside a service
   can then be tied back to the HTTP request that caused it — including the
   response header returned to the client. Without this, a 500 in the logs has no
   user-facing counterpart to match it against.

Redaction happens in the *formatter*, not at each call site. A password or token
that reaches `extra=` is scrubbed even when the developer forgot, which is the
only version of this rule that survives contact with a deadline.
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

from app.core.config import settings

# --------------------------------------------------------------------------
# Request correlation
# --------------------------------------------------------------------------
# A ContextVar rather than a global: it is per-async-task, so concurrent
# requests cannot see each other's ids.
_request_id: ContextVar[str | None] = ContextVar("tripmate_request_id", default=None)


def set_request_id(value: str | None):
    """Bind a request id to the current task. Returns the reset token."""
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def get_request_id() -> str | None:
    return _request_id.get()


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------
# Matched as a case-insensitive *substring* of the key, so `refresh_token` and
# `access_token_hash` are both caught by "token".
#
# Deliberately narrow: a hint like "code" would also redact `status_code`, and an
# over-eager redactor gets switched off by the first person it inconveniences.
_SENSITIVE_HINTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "private_key",
    "credential",
    "otp",
)

REDACTED = "[redacted]"

# Attributes every LogRecord carries. Anything else came from `extra=` and is
# promoted to a top-level field.
_RESERVED = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "stacklevel", "thread", "threadName",
        "taskName",
    }
)


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SENSITIVE_HINTS)


def redact(value):
    """Recursively replace sensitive values, returning a JSON-safe structure.

    Applied to the whole payload rather than to a known list of fields, so a
    nested `{"user": {"password": ...}}` is caught too.
    """
    if isinstance(value, dict):
        return {
            key: (REDACTED if is_sensitive_key(str(key)) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _extra_fields(record: logging.LogRecord) -> dict:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _RESERVED and not key.startswith("_")
    }


class JsonFormatter(logging.Formatter):
    """One JSON object per record — for log collectors, not for humans."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id

        payload.update(_extra_fields(record))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(redact(payload), ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """Readable single line, with the request id shortened to 8 characters.

    The full id is still in the response header; the short form is only there to
    let you eyeball which lines belong together.
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        request_id = get_request_id()
        prefix = f"[{request_id[:8]}] " if request_id else ""
        line = (
            f"{self.formatTime(record, '%H:%M:%S')} "
            f"{record.levelname:<8} {record.name} {prefix}{record.getMessage()}"
        )

        extras = _extra_fields(record)
        if extras:
            line += " " + json.dumps(redact(extras), ensure_ascii=False, default=str)

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


# --------------------------------------------------------------------------
# Installation
# --------------------------------------------------------------------------
_HANDLER_NAME = "tripmate-structured"

# uvicorn's own access log would duplicate the structured `http_request` event,
# in a different format and without the request id. Ours replaces it.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def setup_logging() -> None:
    """Install the root handler. Safe to call more than once."""
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.use_json_logs else TextFormatter())
    # Tagged so a second call (uvicorn reload, tests) replaces ours rather than
    # stacking duplicates — and so we never remove a handler we did not add
    # (pytest installs its own capture handler on the root logger).
    handler.name = _HANDLER_NAME

    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "name", None) == _HANDLER_NAME:
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # Route uvicorn through our handler so every line has the same shape.
    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
        logger.setLevel(level)

    # SQLAlchemy's engine logger emits a line per statement at INFO, which buries
    # everything else. DB_ECHO is the explicit switch for that.
    if not settings.DB_ECHO:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
