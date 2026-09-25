"""Tests for settings guards — the configuration that fails *silently*.

The settings most worth testing are not the ones that crash. They are the ones
that let the application run perfectly well while a security guarantee quietly
disappears: a wrong value that produces no error, no log line and no failing
request is exactly the value that reaches production.

`COOKIE_DOMAIN` is the current example. It must be empty, so that `set_cookie`
omits the `Domain` attribute and the refresh cookie is host-only. Nothing else in
the codebase reads it (see `api/v1/auth.py`), so a production deployment could
set it, lose the guarantee, and never find out. The guard therefore fails at
settings-construction time — the process refuses to start.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _settings(**overrides) -> Settings:
    """Build Settings without reading the developer's real `.env`.

    `_env_file=None` is load-bearing: without it a local `.env` (which certainly
    sets `ENV=development`) would override the `ENV` passed here, and every
    "production" case below would silently test the development path instead.
    """
    return Settings(_env_file=None, **overrides)


# --- the guard holds -------------------------------------------------------


def test_production_rejects_a_cookie_domain():
    """The whole point: fail loudly rather than silently lose host-only cookies."""
    with pytest.raises(ValidationError) as excinfo:
        _settings(ENV="production", COOKIE_DOMAIN=".example.com")

    # Assert on the field, not the prose — a reworded message must not turn a
    # security test into a failure, but the wrong field must still be caught.
    errors = excinfo.value.errors()
    assert [e["loc"] for e in errors] == [("COOKIE_DOMAIN",)]


# --- the guard does not over-reach ----------------------------------------


def test_production_accepts_an_empty_cookie_domain():
    """The intended production configuration must keep working."""
    assert _settings(ENV="production", COOKIE_DOMAIN="").COOKIE_DOMAIN == ""


def test_development_may_set_a_cookie_domain():
    """Relaxed locally on purpose: one host serving several ports is a normal
    dev setup, and a guard that fires during development gets worked around."""
    assert _settings(ENV="development", COOKIE_DOMAIN=".example.com").COOKIE_DOMAIN == (
        ".example.com"
    )


def test_staging_may_set_a_cookie_domain():
    """Only production is guarded — staging is not yet a place where the
    guarantee is load-bearing, and over-guarding trains people to disable it."""
    assert _settings(ENV="staging", COOKIE_DOMAIN=".example.com").COOKIE_DOMAIN == (
        ".example.com"
    )


def test_defaults_are_host_only():
    """The safe value is the default: a missing setting must not be a weakness."""
    assert _settings().COOKIE_DOMAIN == ""
