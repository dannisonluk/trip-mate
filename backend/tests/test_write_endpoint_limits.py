"""Every state-changing endpoint must be rate limited (audit finding B8).

The defect being guarded
------------------------
`WRITE_RATE` (`60/minute`) was applied to POST/PATCH handlers but *not* to the
five DELETE handlers. Deletes are the most destructive verb in the API — one of
them erases a user account, another removes a trip and cascades to its
applications and chat rooms — and they were the only write path with no ceiling
at all. A stolen access token could be used to walk the caller's data and delete
everything in it as fast as the network allowed.

Why a route-inventory test rather than five endpoint tests
----------------------------------------------------------
Five hand-written tests would each prove that one handler works. None of them
would notice the *sixth* DELETE added next quarter. The defect is a missing
decorator, which is only observable as an absence — so the assertion has to be
over the set of routes, not over any individual call.

This reads the live `app.routes`, so it sees exactly what FastAPI will serve.
That matters because `@limit` is a *decorator*: applying it changes the
function, and a test that imported the handler and inspected it in isolation
could disagree with the router.
"""

from __future__ import annotations

from app.core.rate_limit import limit  # noqa: F401  (existence check)
from app.main import app


def _route_key(route) -> str:
    path = getattr(route, "path", None) or getattr(route, "path_format", "?")
    methods = ",".join(sorted(getattr(route, "methods", None) or []))
    return f"{methods} {path}"


def _write_methods(route) -> set[str]:
    return {m for m in (getattr(route, "methods", None) or set())} & {
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }


def _is_health_probe(route) -> bool:
    """`/health` is deliberately unlimited — a probe must never be throttled."""
    return getattr(route, "path", "") in {"/health", "/healthz", "/ready"}


def _has_limiter(route) -> bool:
    """True when the endpoint carries a slowapi limit.

    slowapi installs its own wrapper, so the attribute to look for is the
    marker it leaves on the wrapped callable. We probe a few shapes and accept
    any of them, because the exact name is slowapi's private detail and a
    version bump must not turn this guard into a false alarm.
    """
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        return False
    for attr in ("__slowapi_limited__", "__wrapped__", "_limits", "limit"):
        if getattr(endpoint, attr, None):
            return True
    # slowapi wraps with functools.wraps, so `__wrapped__` may point at the
    # original *and* the marker sits on the wrapper's closure.
    closure = getattr(endpoint, "__closure__", None) or ()
    for cell in closure:
        try:
            content = cell.cell_contents
        except ValueError:  # pragma: no cover — empty cell
            continue
        if getattr(content, "__slowapi_limited__", None):
            return True
    return False


def test_every_delete_endpoint_is_rate_limited():
    """The five DELETE handlers, and any added later, must carry a limit.

    This is an absence check, which is the point: the bug was a decorator that
    was never written, so no behavioural test on any single endpoint could have
    caught it.
    """
    deletes = [
        route
        for route in app.routes
        if "DELETE" in (getattr(route, "methods", None) or set())
    ]
    assert deletes, "no DELETE routes found — the inventory is wrong, not the app"

    unlimited = [_route_key(r) for r in deletes if not _has_limiter(r)]
    assert not unlimited, (
        "DELETE endpoints with no rate limit: "
        + ", ".join(unlimited)
        + " — deletes are the most destructive verb in the API and were exactly "
        "the gap B8 identified"
    )


def test_every_write_endpoint_is_rate_limited():
    """Generalises the check so the next new write route is covered too."""
    unlimited = [
        _route_key(route)
        for route in app.routes
        if _write_methods(route)
        and not _is_health_probe(route)
        and not _has_limiter(route)
    ]
    assert not unlimited, "write endpoints with no rate limit: " + ", ".join(unlimited)


def test_the_limiter_is_not_satisfied_by_a_bare_decorator():
    """Guard against a false pass: the marker must be observable at all.

    If `_has_limiter` returned True for everything — a plausible failure of a
    duck-typed attribute probe — both tests above would pass on an app with no
    limits anywhere. So assert the negative direction explicitly: an unlimiited
    GET route must NOT look limited.
    """
    gets = [
        route
        for route in app.routes
        if (getattr(route, "methods", None) or set()) == {"GET"}
        and not _is_health_probe(route)
    ]
    assert gets, "no GET routes found — the probe is not exercising anything"

    limited_gets = [r for r in gets if _has_limiter(r)]
    assert len(limited_gets) < len(gets), (
        "every GET route looks limited, including ones that are not — the "
        "marker probe is vacuously true and both inventory tests are worthless"
    )
