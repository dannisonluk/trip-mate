"""Trip Mate API — application entrypoint.

Wires together:
  * Structured logging + request correlation + metrics (observability)
  * CORS (explicit origins only, §5.2)
  * Security headers (§4.1 HSTS, §5.1 CSP / X-Frame-Options / nosniff)
  * Rate limiting (§3.2)
  * REST routers + WebSocket router
  * Optional local media serving
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

# --- Logging first ---------------------------------------------------------
# The log handler must be installed before importing anything that can emit a
# record at import time — `core/rate_limit.py` probes its storage backend on
# import and logs the result, so a stray unstructured line would otherwise be
# printed before the formatter exists.
from app.core.config import settings  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402

setup_logging()

# Everything below is imported after the handler is in place.
from anyio import to_thread  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

from app.api.v1.router import api_router  # noqa: E402
from app.core import metrics  # noqa: E402
from app.core.middleware import RequestContextMiddleware, server_error_handler  # noqa: E402
from app.core.rate_limit import SLOWAPI_AVAILABLE, limiter  # noqa: E402
from app.db.session import init_db  # noqa: E402
from app.services import sms  # noqa: E402
from app.services.storage import local_media_dir  # noqa: E402
from app.ws.routes import router as ws_router  # noqa: E402

logger = logging.getLogger("tripmate")

SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob: https:; "
        "script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self' wss: https:; frame-ancestors 'none'; base-uri 'self'"
    ),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.DATABASE_URL.startswith("sqlite"):
        await init_db()
    if settings.STORAGE_BACKEND == "local":
        local_media_dir()

    # --- Bound the password-hashing thread pool ---------------------------
    # Argon2 is offloaded to a worker thread (see core/security.py), which is
    # what keeps the event loop responsive during logins. anyio defaults to 40
    # worker threads, but each Argon2 call reserves memory_cost (64 MiB) — 40
    # concurrent hashes would peak near 2.5 GB and can OOM a small container.
    # Cap it explicitly instead of inheriting the default.
    to_thread.current_default_thread_limiter().total_tokens = (
        settings.PASSWORD_HASH_MAX_CONCURRENCY
    )

    # --- Production readiness warnings ------------------------------------
    # These are the settings that fail silently: the app runs fine, but a
    # security or delivery guarantee is quietly missing.
    if settings.is_production:
        if settings.JWT_ALGORITHM.upper() == "HS256":
            logger.warning(
                "Running production with HS256 — configure RS256 keys (JWT_PRIVATE_KEY)."
            )
        if settings.OTP_DEV_ECHO:
            logger.warning(
                "OTP_DEV_ECHO is set but ignored in production (codes are never returned)."
            )
        if not settings.COOKIE_SECURE:
            logger.warning("COOKIE_SECURE is false in production — refresh cookies are not HTTPS-only.")

    ready, reason = sms.provider_is_production_ready()
    if not ready:
        (logger.error if settings.is_production else logger.info)(
            "OTP delivery: %s", reason
        )

    yield


app = FastAPI(
    title=f"{settings.PROJECT_NAME} API",
    version="1.0.0",
    description=(
        "Travel-companion matching platform for Hong Kong. "
        "See docs/SECURITY.md for the security & privacy contract."
    ),
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url=None,
)

if SLOWAPI_AVAILABLE and limiter is not None:
    app.state.limiter = limiter
    try:
        from slowapi.errors import RateLimitExceeded
        from slowapi import _rate_limit_exceeded_handler

        def _rate_limited(request: Request, exc: Exception) -> Response:
            """Count the trip, then hand off to slowapi's own 429 response.

            This wrapper exists because the handler is the *only* place that knows
            a request was rejected by the limiter. `tripmate_rate_limit_tripped_total`
            was declared and exposed on /metrics but never incremented, so it read
            0 forever — worse than not having it, because a dashboard showing zero
            rate-limit trips during a credential-stuffing attempt is actively
            misleading.
            """
            metrics.observe_rate_limit_trip()
            return _rate_limit_exceeded_handler(request, exc)

        app.add_exception_handler(RateLimitExceeded, _rate_limited)
    except Exception:  # noqa: BLE001
        pass

# Unhandled exceptions: log them structurally with the request id, and hand the
# id back to the client so a bug report is searchable.
app.add_exception_handler(Exception, server_error_handler)

# --- CORS: exact origins only, credentials allowed (§5.2) -------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if settings.is_production:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


# Added LAST on purpose: `add_middleware` prepends, so this is the outermost
# layer and therefore sees every response — including ones produced by the two
# middlewares above — and can stamp `X-Request-ID` on all of them.
app.add_middleware(RequestContextMiddleware)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "service": settings.PROJECT_NAME, "env": settings.ENV}


@app.get("/api/v1/disclaimer", tags=["meta"])
def disclaimer():
    """Single source of truth for the legal disclaimer shown across the UI (§6)."""
    return {
        "platform": settings.PROJECT_NAME,
        "text": (
            "本平台僅提供資訊媒合服務，線下見面與旅遊期間之個人人身安全、財物損失及消費糾紛，"
            "平台概不承擔法律責任。切勿在未建立信任前進行金錢交易。"
        ),
    }


if settings.METRICS_ENABLED:
    # Unauthenticated by design — a Prometheus scraper does not carry a JWT. It is
    # therefore opt-in (`METRICS_ENABLED`) and must be reachable only from an
    # internal listener or an allowlist, never from the public ingress. Labels are
    # route templates, so the payload reveals traffic shape but no user data.
    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics():
        payload, content_type = metrics.render()
        return Response(content=payload, headers={"Content-Type": content_type})


app.include_router(api_router, prefix=settings.API_V1_PREFIX)
# WebSocket lives under the same version prefix: /api/v1/ws/chat/{room_id}
app.include_router(ws_router, prefix=settings.API_V1_PREFIX)

if settings.STORAGE_BACKEND == "local":
    from fastapi.staticfiles import StaticFiles

    app.mount("/media", StaticFiles(directory=str(local_media_dir())), name="media")
