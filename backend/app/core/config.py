"""Application configuration (12-factor, loaded from environment / .env)."""
from functools import lru_cache
from typing import List, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- App ---------------------------------------------------------------
    ENV: Literal["development", "staging", "production"] = "development"
    PROJECT_NAME: str = "Trip Mate"
    API_V1_PREFIX: str = "/api/v1"
    FRONTEND_URL: str = "http://localhost:3000"

    # --- Security ----------------------------------------------------------
    SECRET_KEY: str = "CHANGE_ME_dev_only"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    JWT_PRIVATE_KEY: str = ""
    JWT_PUBLIC_KEY: str = ""

    # Empty string → host-only cookie (no Domain attribute), which is the
    # recommended default: the refresh cookie is only ever sent back to the API
    # host, so it is not exposed to sibling subdomains. Set it (e.g.
    # ".example.com") only when the API is served from a different subdomain
    # than the one that must present the cookie.
    COOKIE_DOMAIN: str = ""
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "strict"

    # Argon2 runs in a worker thread (it releases the GIL). Each concurrent hash
    # reserves memory_cost = 64 MiB, so this cap is a memory bound, not just a
    # throughput knob: 40 threads (anyio's default) could peak near 2.5 GB.
    PASSWORD_HASH_MAX_CONCURRENCY: int = 8

    # Comma-separated exact origins. NEVER use "*".
    BACKEND_CORS_ORIGINS: str = "http://localhost:3000"

    # --- Database (async driver required) ----------------------------------
    # Production: postgresql+asyncpg://user:pass@host:5432/db
    # Local dev : sqlite+aiosqlite:///./tripmate.db
    DATABASE_URL: str = "sqlite+aiosqlite:///./tripmate.db"
    DB_ECHO: bool = False

    # --- Rate limiting / cache / OTP ---------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"
    RATE_LIMIT_ENABLED: bool = True
    OTP_TTL_SECONDS: int = 300
    # When true (development only) the OTP is returned in the API response
    # instead of being sent over SMS, so the flow is testable without an SMS gateway.
    # Forced off in production regardless of this value.
    OTP_DEV_ECHO: bool = True

    # --- SMS delivery (OTP) ------------------------------------------------
    # console : log the code (development only — nobody receives an SMS)
    # webhook : POST {phone_number, code, sender_id} to SMS_WEBHOOK_URL
    SMS_PROVIDER: Literal["console", "webhook"] = "console"
    SMS_WEBHOOK_URL: str = ""
    SMS_WEBHOOK_TOKEN: str = ""      # sent as `Authorization: Bearer <token>`
    SMS_SENDER_ID: str = "TripMate"
    SMS_TIMEOUT_SECONDS: float = 10.0

    # --- Content moderation ------------------------------------------------
    # On by default: the local rule set is deterministic, offline and cheap, so
    # there is no operational reason to run without it.
    CONTENT_FILTER_ENABLED: bool = True
    # local   : local rules only (no network, no third-party data processor)
    # webhook : local rules, then POST {text, field, locale} to CONTENT_WEBHOOK_URL
    #           — the integration point for a real moderation API (a thin adapter
    #           that speaks your vendor's protocol, rather than teaching this
    #           module every vendor's schema).
    CONTENT_FILTER_PROVIDER: Literal["local", "webhook"] = "local"
    CONTENT_WEBHOOK_URL: str = ""
    CONTENT_WEBHOOK_TOKEN: str = ""   # sent as `Authorization: Bearer <token>`
    CONTENT_WEBHOOK_TIMEOUT_SECONDS: float = 5.0
    # A provider that is slow must not hold a user's request open. On timeout or
    # any error the content is allowed through and the failure is logged — the
    # same fail-open reasoning as the rate limiter: a moderation outage must not
    # become a total write outage.
    CONTENT_FILTER_FAIL_OPEN: bool = True

    # --- Observability -----------------------------------------------------
    LOG_LEVEL: str = "INFO"
    # auto : JSON in production, human-readable text elsewhere.
    # JSON is for log collectors; text is for the person reading a terminal.
    LOG_FORMAT: Literal["auto", "json", "text"] = "auto"
    # Exposes /metrics in Prometheus text format. Off by default because the
    # endpoint is unauthenticated by design (scrapers do not carry JWTs), so it
    # must sit behind an internal listener or an allowlist — opt in deliberately.
    METRICS_ENABLED: bool = False
    # Trust X-Forwarded-For / X-Real-IP when recording an audit entry's IP.
    # Only enable behind a proxy that *overwrites* those headers; otherwise a
    # client can forge its own source address in the audit trail.
    TRUST_PROXY_HEADERS: bool = False

    # --- Storage -----------------------------------------------------------
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    S3_ENDPOINT_URL: str = ""
    S3_REGION: str = "auto"
    S3_BUCKET: str = "tripmate-uploads"
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_PUBLIC_BASE_URL: str = ""
    PRESIGN_EXPIRE_SECONDS: int = 300
    MAX_UPLOAD_BYTES: int = 5 * 1024 * 1024
    LOCAL_UPLOAD_DIR: str = "./var/uploads"

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.BACKEND_CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"

    @property
    def use_json_logs(self) -> bool:
        if self.LOG_FORMAT == "json":
            return True
        if self.LOG_FORMAT == "text":
            return False
        return self.is_production

    @property
    def jwt_private_key(self) -> str:
        return self.JWT_PRIVATE_KEY.replace("\\n", "\n")

    @property
    def jwt_public_key(self) -> str:
        return self.JWT_PUBLIC_KEY.replace("\\n", "\n")

    @property
    def sync_database_url(self) -> str:
        """Sync URL for Alembic (strips the async driver)."""
        return (
            self.DATABASE_URL.replace("+asyncpg", "+psycopg")
            .replace("+aiosqlite", "")
        )

    @field_validator("COOKIE_DOMAIN")
    @classmethod
    def _cookie_domain_must_be_empty_in_production(cls, value: str, info) -> str:
        """Refuse a production `COOKIE_DOMAIN`.

        The refresh cookie is intentionally host-only: an empty value makes
        `set_cookie` omit the `Domain` attribute entirely, so the cookie is sent
        back only to the host that set it and never to a sibling subdomain (a
        hijacked subdomain is the classic escalation path — it could otherwise
        present the refresh cookie to the API).

        Nothing else in the codebase observes this setting, which is exactly why
        it needs a guard: a production deployment that sets `COOKIE_DOMAIN` would
        run perfectly well while silently losing that guarantee. Failing at
        settings-construction means the process refuses to start, rather than
        discovering the weakness during an incident.

        Deliberately NOT solved by renaming the cookie with the `__Host-` prefix:
        that prefix requires `Path=/` and *no* `Domain`, while the refresh cookie's
        path is deliberately narrowed to `/api/v1/auth` to shrink its exposure to
        CSRF. The prefix is therefore spec-incompatible with the current design,
        and switching to it would trade a genuine CSRF narrowing for a
        configuration guard that this validator provides anyway.
        """
        if value and info.data.get("ENV") == "production":
            raise ValueError(
                "COOKIE_DOMAIN must be empty in production: the refresh cookie is "
                "deliberately host-only. Setting a Domain would send it to every "
                "sibling subdomain. Remove COOKIE_DOMAIN from the environment."
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
