"""Prometheus metrics for the HTTP surface.

Deliberately tiny: the four signals that answer "is this deployment healthy, and
where is the latency", not a general instrumentation framework.

`prometheus_client` is imported defensively, matching how `slowapi` is treated in
`core/rate_limit.py` — a missing optional dependency must not stop the service
from booting. When it is absent, `AVAILABLE` is False and `/metrics` is not
mounted; every `observe_*` helper becomes a no-op.

Label cardinality is the trap here. `route` is the *matched route template*
(`/api/v1/trips/{trip_id}`), never the raw path — labelling by raw path would
create one time series per trip id and take the monitoring system down instead of
helping it. The same reasoning excludes user ids, request ids and query strings.
"""
from __future__ import annotations

from app.core.config import settings

try:  # pragma: no cover - exercised by whichever branch is installed
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    AVAILABLE = True
except ImportError:  # pragma: no cover
    AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

# A private registry: the default one also collects process/GC metrics, which
# are noisy here and would change /metrics output between environments.
registry = CollectorRegistry() if AVAILABLE else None

if AVAILABLE:
    HTTP_REQUESTS = Counter(
        "tripmate_http_requests_total",
        "HTTP requests handled, by route template, method and status code.",
        ["route", "method", "status"],
        registry=registry,
    )
    HTTP_DURATION = Histogram(
        "tripmate_http_request_duration_seconds",
        "HTTP request latency in seconds, by route template and method.",
        ["route", "method"],
        registry=registry,
        # Buckets chosen around the endpoint classes that exist: an indexed query
        # is well under 50 ms, an Argon2 login sits near 40 ms, and anything past
        # a second is a problem rather than a data point.
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    )
    AUDIT_EVENTS = Counter(
        "tripmate_audit_events_total",
        "Security-relevant actions recorded in the audit trail, by action.",
        ["action"],
        registry=registry,
    )
    RATE_LIMIT_TRIPS = Counter(
        "tripmate_rate_limit_tripped_total",
        "Requests rejected by the rate limiter.",
        registry=registry,
    )
    CONTENT_BLOCKED = Counter(
        "tripmate_content_blocked_total",
        "Writes rejected by the content filter, by field and rule.",
        # `field` and `reason` are both closed sets (the Field literal and the
        # rule codes), so cardinality stays bounded. Never label by the matched
        # text or the author.
        ["field", "reason"],
        registry=registry,
    )
    CONTENT_FLAGGED = Counter(
        "tripmate_content_flagged_total",
        "Writes allowed but flagged for review, by field and rule.",
        ["field", "reason"],
        registry=registry,
    )
    CONTENT_PROVIDER_ERRORS = Counter(
        "tripmate_content_provider_errors_total",
        "Times the external moderation provider could not be reached or answered.",
        registry=registry,
    )
    APP_INFO = Gauge(
        "tripmate_app_info",
        "Static build/deployment information; the value is always 1.",
        ["env", "version"],
        registry=registry,
    )
    APP_INFO.labels(env=settings.ENV, version="1.0.0").set(1)


def observe_request(route: str, method: str, status: int, duration_seconds: float) -> None:
    if not AVAILABLE:
        return
    HTTP_REQUESTS.labels(route=route, method=method, status=str(status)).inc()
    HTTP_DURATION.labels(route=route, method=method).observe(duration_seconds)


def observe_audit_event(action: str) -> None:
    if not AVAILABLE:
        return
    AUDIT_EVENTS.labels(action=action).inc()


def observe_rate_limit_trip() -> None:
    if not AVAILABLE:
        return
    RATE_LIMIT_TRIPS.inc()


def observe_content_blocked(field: str, reason: str) -> None:
    if not AVAILABLE:
        return
    CONTENT_BLOCKED.labels(field=field, reason=reason).inc()


def observe_content_flagged(field: str, reason: str) -> None:
    if not AVAILABLE:
        return
    CONTENT_FLAGGED.labels(field=field, reason=reason).inc()


def observe_content_provider_error() -> None:
    if not AVAILABLE:
        return
    CONTENT_PROVIDER_ERRORS.inc()


def render() -> tuple[bytes, str]:
    """Latest exposition payload and its content type."""
    if not AVAILABLE:
        return b"", CONTENT_TYPE_LATEST
    return generate_latest(registry), CONTENT_TYPE_LATEST
