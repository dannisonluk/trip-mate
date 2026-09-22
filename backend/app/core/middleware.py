"""HTTP middleware: request correlation, access logging, metrics.

Order matters. `add_middleware` *prepends*, so the last middleware added is the
outermost — and this one wants to be outermost so that it observes every
response, including CORS preflight rejections produced by the middleware below
it, and so that `X-Request-ID` is present on all of them.
"""
from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core import metrics
from app.core.logging import get_request_id, reset_request_id, set_request_id
from app.core.request_meta import (
    client_ip,
    log_path,
    route_template,
    sanitise_request_id,
)

logger = logging.getLogger("tripmate.http")

REQUEST_ID_HEADER = "X-Request-ID"

# Scraped every few seconds by a monitoring agent; logging each scrape would
# drown the access log it is supposed to help you read. Still counted in metrics.
_QUIET_PATHS = frozenset({"/metrics"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Give every request an id, one structured log line, and a duration sample."""

    async def dispatch(self, request: Request, call_next):
        request_id = (
            sanitise_request_id(request.headers.get(REQUEST_ID_HEADER))
            or uuid.uuid4().hex
        )
        token = set_request_id(request_id)
        # Also on `request.state`: the 500 handler runs *outside* this middleware,
        # after the ContextVar has been reset, and reads the id from the scope.
        request.state.request_id = request_id

        started = time.perf_counter()
        method = request.method
        path = log_path(request)
        ip = client_ip(request)

        try:
            response = await call_next(request)
        except Exception:
            # Deliberately not logged here. `ServerErrorMiddleware` sits outside
            # this middleware and calls the registered 500 handler, which has both
            # the exception and the request — logging in both places would
            # double-report every unhandled failure.
            duration = time.perf_counter() - started
            metrics.observe_request(route_template(request), method, 500, duration)
            raise

        duration = time.perf_counter() - started
        status = response.status_code
        # Read the route template *after* the call: `scope["route"]` is only set
        # once routing has happened, so sampling it beforehand labels every
        # request `<unmatched>` — including the ones that matched perfectly.
        route = route_template(request)
        metrics.observe_request(route, method, status, duration)

        if request.url.path not in _QUIET_PATHS:
            logger.log(
                # 4xx is the caller's problem, 5xx is ours. Keeping client errors
                # at INFO means a burst of 401s from a credential-stuffing attempt
                # does not look like an outage in an error-rate dashboard.
                logging.WARNING if status >= 500 else logging.INFO,
                "http_request",
                extra={
                    "method": method,
                    "path": path,
                    "route": route,
                    "status": status,
                    "duration_ms": round(duration * 1000, 2),
                    "ip": ip,
                },
            )

        response.headers.setdefault(REQUEST_ID_HEADER, request_id)
        reset_request_id(token)
        return response


async def server_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Structured log + request id for unhandled exceptions.

    Registered for `Exception`, which FastAPI/Starlette route to
    `ServerErrorMiddleware`. That middleware re-raises after this returns, so
    `TestClient(raise_server_exceptions=True)` still surfaces the traceback
    instead of silently turning a bug into an assertion on a 500 body.

    The client gets the request id back in the body: without it, a user reporting
    "it broke" gives you nothing to search the logs for.
    """
    request_id = getattr(request.state, "request_id", None) or get_request_id()
    logger.exception(
        "unhandled_exception",
        extra={
            "method": request.method,
            "path": log_path(request),
            "route": route_template(request),
            "exception_type": type(exc).__name__,
        },
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id},
    )
