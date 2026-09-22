"""Request metadata extraction shared by the access log and the audit trail.

Kept in one place so the access log and an audit entry can never disagree about
who the caller was.
"""
from __future__ import annotations

from fastapi import Request

from app.core.config import settings

# A client-supplied header must never be able to inject a fake log line, so the
# accepted alphabet is deliberately tiny.
_REQUEST_ID_ALLOWED = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
REQUEST_ID_MAX_LENGTH = 64
_MAX_PATH_LOG_LENGTH = 200
_MAX_USER_AGENT_LENGTH = 300


def sanitise_request_id(value: str | None) -> str | None:
    """Return a safe request id, or None if the client sent something unusable.

    `X-Request-ID` is echoed into a response header and written into every log
    line for the request, so an unchecked value is a log-injection vector — a
    newline plus a forged `"level": "CRITICAL"` is all it takes in the text
    formatter. Anything outside `[A-Za-z0-9._-]`, or over-long, is rejected and
    a fresh id is generated instead.
    """
    if not value:
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > REQUEST_ID_MAX_LENGTH:
        return None
    if any(char not in _REQUEST_ID_ALLOWED for char in candidate):
        return None
    return candidate


def client_ip(request: Request) -> str | None:
    """Best-effort source address.

    `X-Forwarded-For` is only consulted when `TRUST_PROXY_HEADERS` is on, because
    the header is client-controlled: honouring it without a proxy that overwrites
    it would let anyone forge their own address in the audit trail.
    """
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64] or None
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()[:64] or None

    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent")
    if not value:
        return None
    return value[: _MAX_USER_AGENT_LENGTH - 1] + "…" if len(value) > _MAX_USER_AGENT_LENGTH else value


def log_path(request: Request) -> str:
    """Path for the access log, truncated so a pathological URL cannot flood it."""
    path = request.url.path
    return path if len(path) <= _MAX_PATH_LOG_LENGTH else path[:_MAX_PATH_LOG_LENGTH] + "…"


def route_template(request: Request) -> str:
    """The matched route pattern (e.g. `/api/v1/trips/{trip_id}`).

    Used as a metric label instead of the raw path: `/trips/<uuid>` would create
    one time series per trip, which is how a metrics endpoint takes down the
    monitoring system instead of helping it.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if template:
        return template
    # Unmatched (404) — collapse everything under the prefix to one series.
    path = request.url.path
    prefix = settings.API_V1_PREFIX
    return f"{prefix}/<unmatched>" if path.startswith(prefix) else "<unmatched>"
