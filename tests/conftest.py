"""
Ensures test-specific settings (isolated DB file, non-default JWT secret)
are in place before ANY app module is imported. pytest loads conftest.py
at collection time, before importing test modules, so this reliably wins
the race against app.core.config's lru_cache'd get_settings().
"""
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_saeos.db")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """
    app.ratelimit.limiter.get_rate_limiter() is deliberately lru_cache'd
    (its whole job is a counter dict that persists across requests within
    a process -- see that module's docstring). Across the *test suite*
    that same persistence would let request counts leak between unrelated
    tests, which mostly doesn't matter (each test uses a fresh tenant/user)
    but would be a real, confusing source of flakiness for any test that
    calls a rate-limited endpoint more than a couple of times. Clearing
    the cache before each test gives every test a fresh limiter, the same
    way a fresh process would in production.
    """
    from app.ratelimit.limiter import get_rate_limiter
    get_rate_limiter.cache_clear()
    yield
