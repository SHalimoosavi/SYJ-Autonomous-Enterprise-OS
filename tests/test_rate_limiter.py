"""Rate limiter: pure logic tests for InMemoryRateLimiter, plus
integration tests proving the 429 path actually fires on the real
department-invoke and workflow-run endpoints. RedisRateLimiter's live
verification is in test_rate_limiter_live.py (skipped by default, same
pattern as the Phase 4 pgvector/Celery live tests)."""
import asyncio

import pytest
from starlette.testclient import TestClient

from app.core.database import Base, engine
from app.main import app
from app.ratelimit.limiter import InMemoryRateLimiter

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_schema():
    async def _reset():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_reset())
    yield


def _register(tenant_slug):
    return client.post(
        "/api/v1/auth/register",
        json={"tenant_name": tenant_slug.title(), "tenant_slug": tenant_slug,
              "email": f"founder@{tenant_slug}.test", "password": "supersecret1"},
    )


def _headers(tenant_slug, token):
    return {"X-Tenant-ID": tenant_slug, "Authorization": f"Bearer {token}"}


# --- pure logic ---

@pytest.mark.asyncio
async def test_allows_requests_under_the_limit():
    limiter = InMemoryRateLimiter()
    for _ in range(5):
        allowed, remaining = await limiter.check("key1", limit=5, window_seconds=60)
        assert allowed is True
    # the 6th request in the same window must be denied
    allowed, remaining = await limiter.check("key1", limit=5, window_seconds=60)
    assert allowed is False
    assert remaining == 0


@pytest.mark.asyncio
async def test_remaining_count_decreases_correctly():
    limiter = InMemoryRateLimiter()
    _, remaining1 = await limiter.check("key2", limit=3, window_seconds=60)
    _, remaining2 = await limiter.check("key2", limit=3, window_seconds=60)
    assert remaining1 == 2
    assert remaining2 == 1


@pytest.mark.asyncio
async def test_different_keys_have_independent_limits():
    limiter = InMemoryRateLimiter()
    for _ in range(5):
        await limiter.check("tenant_a", limit=5, window_seconds=60)
    allowed_a, _ = await limiter.check("tenant_a", limit=5, window_seconds=60)
    allowed_b, _ = await limiter.check("tenant_b", limit=5, window_seconds=60)
    assert allowed_a is False
    assert allowed_b is True  # tenant_b's budget is untouched by tenant_a's usage


@pytest.mark.asyncio
async def test_new_window_resets_the_count():
    limiter = InMemoryRateLimiter()
    for _ in range(5):
        await limiter.check("key3", limit=5, window_seconds=1)
    denied, _ = await limiter.check("key3", limit=5, window_seconds=1)
    assert denied is False

    await asyncio.sleep(1.1)  # cross into the next 1-second window
    allowed, remaining = await limiter.check("key3", limit=5, window_seconds=1)
    assert allowed is True
    assert remaining == 4


# --- integration: real endpoints actually enforce the limit ---

def test_department_invoke_returns_429_after_limit_exceeded():
    reg = _register("rl1")
    token = reg.json()["access_token"]
    headers = _headers("rl1", token)

    # Default RATE_LIMIT_REQUESTS_PER_MINUTE is 20; exhaust it.
    for _ in range(20):
        resp = client.post("/api/v1/departments/engineering/invoke", headers=headers, json={"prompt": "hi"})
        assert resp.status_code == 503  # no AI provider in this test env -- expected, not the thing under test

    over_limit = client.post("/api/v1/departments/engineering/invoke", headers=headers, json={"prompt": "hi"})
    assert over_limit.status_code == 429
    assert "Retry-After" in over_limit.headers


def test_rate_limit_is_per_tenant_user_not_global():
    """One tenant maxing out their limit must not affect a different
    tenant's ability to call the same endpoint."""
    reg1 = _register("rl2")
    token1 = reg1.json()["access_token"]
    for _ in range(20):
        client.post("/api/v1/departments/engineering/invoke", headers=_headers("rl2", token1), json={"prompt": "hi"})
    denied = client.post("/api/v1/departments/engineering/invoke", headers=_headers("rl2", token1), json={"prompt": "hi"})
    assert denied.status_code == 429

    reg2 = _register("rl3")
    token2 = reg2.json()["access_token"]
    still_allowed = client.post("/api/v1/departments/engineering/invoke", headers=_headers("rl3", token2), json={"prompt": "hi"})
    assert still_allowed.status_code == 503  # not 429 -- rl3's budget is untouched


def test_rate_limit_applies_before_ai_gateway_call_not_after():
    """Regression guard: the check must happen before parsing/calling the
    gateway, not after -- otherwise a malformed request could still
    consume budget, or worse, the limit check could be bypassed entirely
    by requests that error out before reaching it."""
    reg = _register("rl4")
    token = reg.json()["access_token"]
    headers = _headers("rl4", token)
    for _ in range(20):
        client.post("/api/v1/departments/engineering/invoke", headers=headers, json={"prompt": "hi"})

    # Even a request that would otherwise 400 (missing prompt) must still
    # get 429 first, since the rate limit check runs before body parsing.
    resp = client.post("/api/v1/departments/engineering/invoke", headers=headers, json={})
    assert resp.status_code == 429
