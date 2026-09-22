"""Observability tests: redaction, request correlation, log-injection defence,
and metric cardinality.

These are the properties that are invisible when they break. A redactor that
stops matching still logs happily; a request id that is not propagated still
produces lines; a metric labelled by raw path still returns 200 — it just creates
one time series per row id until the monitoring system falls over. So each test
here asserts on the *absence* of the bad thing, not just the presence of a good
one.
"""
import asyncio
import json
import logging
import uuid

import pytest
from starlette.requests import Request

from app.core.logging import (
    REDACTED,
    JsonFormatter,
    TextFormatter,
    get_request_id,
    is_sensitive_key,
    redact,
    reset_request_id,
    set_request_id,
)
from app.core.request_meta import (
    REQUEST_ID_MAX_LENGTH,
    sanitise_request_id,
    user_agent,
)


def _make_record(message: str = "event", **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="tripmate.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _request(headers: dict | None = None, client=("1.2.3.4", 1234)) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": client,
            "headers": [
                (key.lower().encode(), value.encode())
                for key, value in (headers or {}).items()
            ],
        }
    )


# --- redaction -------------------------------------------------------------


def test_sensitive_keys_are_redacted_in_the_json_line():
    payload = json.loads(
        JsonFormatter().format(
            _make_record(password="hunter2", refresh_token="eyJhbGciOi", nickname="Alice")
        )
    )
    assert payload["password"] == REDACTED
    assert payload["refresh_token"] == REDACTED
    assert payload["nickname"] == "Alice"


def test_a_secret_never_reaches_the_rendered_line():
    """Assert on the raw text, not the parsed dict — that is what gets shipped."""
    secret = "super-secret-value"
    line = JsonFormatter().format(_make_record(api_key=secret))
    assert secret not in line


def test_redaction_is_recursive():
    out = redact(
        {
            "user": {"password": "x", "profile": {"nickname": "Alice"}},
            "items": [{"access_token": "y"}],
            "nested": {"deep": {"credential": "z"}},
        }
    )
    assert out["user"]["password"] == REDACTED
    assert out["user"]["profile"]["nickname"] == "Alice"
    assert out["items"][0]["access_token"] == REDACTED
    assert out["nested"]["deep"]["credential"] == REDACTED


def test_a_sensitive_key_redacts_its_whole_subtree():
    """`tokens` matches the `token` hint, so the list is replaced outright.

    Worth pinning: the alternative — descending into a container whose own name
    says "secret" — is how a redactor leaks the one nested field it did not
    anticipate.
    """
    out = redact({"tokens": [{"access_token": "y", "nickname": "Alice"}]})
    assert out == {"tokens": REDACTED}


def test_redaction_leaves_non_strings_alone():
    out = redact({"count": 3, "flag": True, "nothing": None, "ids": [1, 2]})
    assert out == {"count": 3, "flag": True, "nothing": None, "ids": [1, 2]}


def test_sensitive_matching_is_substring_based_but_narrow():
    # Substring matching catches the prefixed variants...
    assert is_sensitive_key("refresh_token")
    assert is_sensitive_key("X-Authorization")
    assert is_sensitive_key("otp_code")
    # ...without the over-reach that gets a redactor switched off. "code" was
    # deliberately left out of the hint list precisely so this stays readable.
    assert not is_sensitive_key("status_code")
    assert not is_sensitive_key("route")
    assert not is_sensitive_key("country")


def test_text_formatter_redacts_too():
    """Both formatters share the redactor; a dev box must not leak either."""
    line = TextFormatter().format(_make_record(secret="oops"))
    assert "oops" not in line
    assert REDACTED in line


# --- request correlation ---------------------------------------------------


def test_request_id_is_visible_to_both_formatters():
    token = set_request_id("abc123def456")
    try:
        assert json.loads(JsonFormatter().format(_make_record()))["request_id"] == "abc123def456"
        assert "[abc123de]" in TextFormatter().format(_make_record())
    finally:
        reset_request_id(token)
    assert get_request_id() is None


def test_json_line_omits_request_id_when_unbound():
    payload = json.loads(JsonFormatter().format(_make_record()))
    assert "request_id" not in payload


def test_concurrent_tasks_do_not_see_each_others_request_id():
    """A ContextVar, not a global — otherwise one request's id lands in another's
    logs, which is worse than having no id at all."""

    async def worker(value: str, out: list):
        set_request_id(value)
        # Yield so the other task gets a turn while this one holds its value.
        await asyncio.sleep(0)
        out.append((value, get_request_id()))
        await asyncio.sleep(0)
        out.append((value, get_request_id()))

    async def scenario():
        out: list = []
        await asyncio.gather(worker("aaa", out), worker("bbb", out))
        return out

    observations = asyncio.run(scenario())
    # Order between the two tasks is not deterministic; the pairing is. Each
    # observation must match the id its own task set.
    assert len(observations) == 4
    for expected, actual in observations:
        assert expected == actual, observations
    assert {value for value, _ in observations} == {"aaa", "bbb"}


# --- log-injection defence -------------------------------------------------


@pytest.mark.parametrize("value", ["abc123", "a-b_c.d", "A" * REQUEST_ID_MAX_LENGTH, "trace-1"])
def test_valid_request_ids_pass_through(value):
    assert sanitise_request_id(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "bad\ninjected",  # newline → a forged second log line
        "bad\r\nINFO forged",
        'quote"inside',
        "a" * (REQUEST_ID_MAX_LENGTH + 1),  # over-long
        "with space",
        "<script>alert(1)</script>",
        "id;rm -rf /",
        "{}",
        "   ",
        "",
    ],
)
def test_unsafe_request_ids_are_rejected(value):
    assert sanitise_request_id(value) is None


def test_request_id_header_is_echoed_back(client):
    resp = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert resp.status_code == 200
    assert resp.headers["X-Request-ID"] == "trace-abc-123"


def test_a_generated_id_is_returned_when_the_client_sends_none(client):
    resp = client.get("/health")
    assert resp.headers["X-Request-ID"]


def test_a_forged_request_id_is_replaced_and_never_logged(client, caplog):
    """The end-to-end version of the unit test above: the injection must not
    survive into the response header or into any log line."""
    forged = "evil\nINFO  tripmate.http http_request status=200"

    with caplog.at_level(logging.INFO, logger="tripmate.http"):
        resp = client.get("/health", headers={"X-Request-ID": forged})

    returned = resp.headers["X-Request-ID"]
    assert returned != forged
    assert "\n" not in returned
    # The forged text must not appear anywhere in the captured log output.
    assert "evil" not in caplog.text
    assert "INFO  tripmate.http http_request status=200" not in caplog.text


def test_access_log_carries_structured_fields_and_the_route_template(client, caplog):
    """The access line must be queryable, not just readable.

    `route` is the matched template — the same value the metric uses — so a log
    search for one trip's requests finds the pattern rather than every other
    trip's line that happens to share it.
    """
    trip_id = uuid.uuid4()
    with caplog.at_level(logging.INFO, logger="tripmate.http"):
        client.get(f"/api/v1/trips/{trip_id}")

    record = next(
        r for r in caplog.records if r.name == "tripmate.http" and r.getMessage() == "http_request"
    )
    assert record.method == "GET"
    # The route template is resolved even though the request was rejected by the
    # auth dependency: routing happens first, so an unauthenticated probe is
    # still labelled with the pattern rather than collapsing to `<unmatched>`.
    assert record.route == "/api/v1/trips/{trip_id}"
    assert record.status == 401
    assert isinstance(record.duration_ms, float)
    # The id is available for a human in `path`, but never in the label.
    assert str(trip_id) in record.path
    assert str(trip_id) not in record.route


# --- metric cardinality ----------------------------------------------------


def test_metrics_label_by_route_template_never_by_raw_path(client):
    """`/trips/<uuid>` must collapse to one series, not one per trip.

    This is the failure mode that does not look like a failure: the endpoint
    returns 200 either way, and only the monitoring system notices.
    """
    from app.core import metrics

    trip_id = uuid.uuid4()
    client.get(f"/api/v1/trips/{trip_id}")

    body = metrics.render()[0].decode()
    assert "/api/v1/trips/{trip_id}" in body
    assert str(trip_id) not in body


def test_unmatched_paths_collapse_to_one_series(client):
    from app.core import metrics

    client.get("/api/v1/definitely-not-a-route")
    body = metrics.render()[0].decode()
    assert "/api/v1/<unmatched>" in body
    assert "definitely-not-a-route" not in body


def test_audit_actions_are_counted_by_action(client, register_user):
    from app.core import metrics

    alice = register_user()
    bob = register_user()
    client.post(f"/api/v1/profiles/{bob['profile']['id']}/block", headers=alice["headers"])

    body = metrics.render()[0].decode()
    assert 'tripmate_audit_events_total{action="USER_BLOCKED"}' in body


def test_metrics_endpoint_is_off_unless_deliberately_enabled(client):
    """Unauthenticated by design, so it must not be exposed by default."""
    assert client.get("/metrics").status_code == 404


# --- request metadata ------------------------------------------------------


def test_forwarded_for_is_ignored_unless_a_proxy_is_trusted(monkeypatch):
    from app.core import request_meta
    from app.core.config import settings

    req = _request({"X-Forwarded-For": "9.9.9.9, 10.0.0.1"})

    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    # Client-controlled, so honouring it here would let anyone forge their own
    # address in the audit trail.
    assert request_meta.client_ip(req) == "1.2.3.4"

    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    assert request_meta.client_ip(req) == "9.9.9.9"


def test_real_ip_header_is_used_as_a_fallback_when_trusted(monkeypatch):
    from app.core import request_meta
    from app.core.config import settings

    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    assert request_meta.client_ip(_request({"X-Real-IP": "8.8.8.8"})) == "8.8.8.8"


def test_user_agent_is_truncated_to_a_bounded_length():
    long_agent = "M" * 500
    result = user_agent(_request({"User-Agent": long_agent}))
    assert len(result) <= 300
    assert result.endswith("…")


def test_user_agent_absent_is_none():
    assert user_agent(_request()) is None


# --- the rate-limit counter must actually be wired --------------------------


def test_the_rate_limit_counter_increments():
    from app.core import metrics

    before = metrics.render()[0].decode()
    metrics.observe_rate_limit_trip()
    after = metrics.render()[0].decode()

    def value(body: str) -> float:
        for line in body.splitlines():
            if line.startswith("tripmate_rate_limit_tripped_total "):
                return float(line.rsplit(" ", 1)[1])
        return 0.0

    assert value(after) == value(before) + 1


def test_the_429_handler_is_wrapped_so_trips_are_counted():
    """Structural, and that is the point.

    `tripmate_rate_limit_tripped_total` was declared and exposed on /metrics but
    never incremented — it read 0 forever. A counter that is always zero is worse
    than an absent one, because a dashboard showing no rate-limit trips during a
    credential-stuffing attempt is actively misleading. Executing the handler
    needs a real slowapi `Limit` and a populated `request.state`, so this asserts
    the wiring instead: if someone reverts to slowapi's default handler, the
    metric goes quiet again and this fails.
    """
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    from app.main import app

    handler = app.exception_handlers.get(RateLimitExceeded)
    assert handler is not None, "no handler registered for RateLimitExceeded"
    assert handler is not _rate_limit_exceeded_handler, (
        "slowapi's default handler is registered, so observe_rate_limit_trip() is "
        "never called and the metric stays at zero"
    )
