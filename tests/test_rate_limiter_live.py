"""
Live integration test for RedisRateLimiter against a real Redis
instance. Skipped by default unless REDIS_LIVE_TEST=true -- same pattern
as test_pgvector_live.py and test_async_workflow_live.py. Proves the
Redis-backed limiter shares state correctly (the entire reason it exists
over the in-memory default: one shared counter across processes, not one
counter per worker).
"""
import asyncio
import os

import pytest

RUN_LIVE = os.environ.get("REDIS_LIVE_TEST") == "true"
pytestmark = pytest.mark.skipif(not RUN_LIVE, reason="REDIS_LIVE_TEST not set -- skipping live Redis test")


@pytest.mark.asyncio
async def test_redis_rate_limiter_enforces_shared_limit_across_instances():
    """Two separate RedisRateLimiter instances (simulating two different
    worker processes) sharing the same Redis must enforce ONE combined
    limit, not one each -- this is the actual bug InMemoryRateLimiter has
    across multiple processes, and what RedisRateLimiter exists to fix."""
    from app.ratelimit.limiter import RedisRateLimiter

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/2")
    limiter_a = RedisRateLimiter(redis_url)
    limiter_b = RedisRateLimiter(redis_url)  # simulates a second process

    key = f"live-test-{os.getpid()}-{id(limiter_a)}"

    # Clean slate: flush this specific test key pattern's window.
    for _ in range(5):
        allowed, remaining = await limiter_a.check(key, limit=5, window_seconds=60)
        assert allowed is True

    # limiter_a has now used the entire budget of 5. limiter_b, sharing
    # the same Redis-backed counter, must see that budget as exhausted --
    # this would incorrectly pass with two separate InMemoryRateLimiter
    # instances, which is exactly the multi-process bug being tested for.
    denied, remaining = await limiter_b.check(key, limit=5, window_seconds=60)
    assert denied is False
    assert remaining == 0


@pytest.mark.asyncio
async def test_redis_rate_limiter_window_expires():
    from app.ratelimit.limiter import RedisRateLimiter

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/2")
    limiter = RedisRateLimiter(redis_url)
    key = f"live-test-expiry-{os.getpid()}"

    for _ in range(3):
        await limiter.check(key, limit=3, window_seconds=1)
    denied, _ = await limiter.check(key, limit=3, window_seconds=1)
    assert denied is False

    await asyncio.sleep(1.2)
    allowed, remaining = await limiter.check(key, limit=3, window_seconds=1)
    assert allowed is True
    assert remaining == 2
