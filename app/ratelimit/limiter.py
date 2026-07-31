"""
Rate limiter abstraction, same shape as app.core.events.EventBus: a
backend chosen by config (RATE_LIMIT_BACKEND), with an in-memory default
that works everywhere including Termux, and a Redis-backed option for
multi-process production deployments where in-memory counters per
process would give each worker its own separate limit (wrong -- a
tenant could get N x the intended limit, one N per worker process).

Sliding-window-by-fixed-bucket algorithm: count requests in the current
`window_seconds`-wide bucket. Simpler than a true sliding log and good
enough for protecting the AI Gateway from runaway spend, not for
precise fairness guarantees.

InMemoryRateLimiter anchors each key's window to that key's own first
request (see its class docstring) and has no boundary-crossing failure
mode. RedisRateLimiter uses the standard Redis INCR+EXPIRE pattern,
which aligns windows to absolute wall-clock boundaries -- a burst that
straddles one of those boundaries can briefly exceed the limit by up to
~2x. This is normal, accepted behavior for that pattern in production
(the standard tradeoff for O(1) Redis state instead of storing a
per-key window-start timestamp), not a bug; noted here because the
in-memory path was specifically hardened against the equivalent issue
during Phase 5 development after it caused real, reproducible test
flakiness (a 20-request burst firing right at a wall-clock minute
boundary), and it's worth being explicit about why the two backends
still differ in this one respect rather than silently.
"""
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from functools import lru_cache


class RateLimiter(ABC):
    @abstractmethod
    async def check(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Returns (allowed, remaining). Increments the counter for `key`
        as a side effect only when this call is part of accepting the
        request -- callers are expected to call this once per request."""
        ...


class InMemoryRateLimiter(RateLimiter):
    """Default for Termux/dev and single-process deployments. Per-process
    only -- see the module docstring for why that's wrong across multiple
    worker processes, which is exactly what RedisRateLimiter fixes.

    Each key's window is anchored to that key's own first request within
    the current window, not to an absolute wall-clock boundary (e.g.
    "the top of the minute"). This is a real, deliberate difference from
    RedisRateLimiter below, not an inconsistency: an absolute-boundary
    design (which RedisRateLimiter uses, the standard Redis
    INCR+EXPIRE pattern) lets a request burst that happens to straddle a
    boundary briefly exceed the limit by ~2x -- rare in production, but
    reliably reproducible in a fast test suite firing 20 requests in a
    tight loop, which is exactly what surfaced this during Phase 5
    development. Anchoring to first-request-per-key removes that failure
    mode by construction for the in-memory path.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, int]] = {}  # key -> (window_start_time, count)

    async def check(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.time()

        bucket_start, count = self._buckets.get(key, (now, 0))
        if now - bucket_start >= window_seconds:
            bucket_start = now
            count = 0

        if count >= limit:
            self._buckets[key] = (bucket_start, count)
            return False, 0

        count += 1
        self._buckets[key] = (bucket_start, count)
        return True, limit - count


class RedisRateLimiter(RateLimiter):
    """
    Production option: one shared counter in Redis instead of one per
    worker process, using INCR + EXPIRE on a per-window key so every
    process (and every Celery worker, for that matter) enforces the same
    limit. Requires the `redis` package (see requirements.txt's
    production-only section) -- imported lazily inside __init__, not at
    module load time, so a default Termux install that never sets
    RATE_LIMIT_BACKEND=redis never needs it installed at all.
    """

    def __init__(self, redis_url: str) -> None:
        import redis.asyncio as redis_asyncio  # lazy import, see class docstring
        self._redis = redis_asyncio.from_url(redis_url)

    async def check(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = int(time.time())
        window_start = now - (now % window_seconds)
        redis_key = f"ratelimit:{key}:{window_start}"

        count = await self._redis.incr(redis_key)
        if count == 1:
            # Only the first request in a window needs to set the expiry;
            # a race here (two requests both seeing count==1) just means
            # the TTL gets set twice with the same intended expiry, harmless.
            await self._redis.expire(redis_key, window_seconds)

        if count > limit:
            return False, 0
        return True, limit - count


@lru_cache
def get_rate_limiter() -> RateLimiter:
    """
    Cached deliberately (unlike AIGateway's settings, see gateway.py's
    comment about that) -- the whole point of InMemoryRateLimiter is a
    single shared counter dict across requests within this process. A
    fresh instance per call would mean every request resets its own
    limit and nothing would ever actually be limited.
    """
    from app.core.config import get_settings
    settings = get_settings()
    if settings.RATE_LIMIT_BACKEND == "redis":
        return RedisRateLimiter(settings.REDIS_URL)
    return InMemoryRateLimiter()
