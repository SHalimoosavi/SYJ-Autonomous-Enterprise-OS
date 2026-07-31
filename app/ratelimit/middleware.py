"""Helper for applying the rate limiter from a route handler. Not
Starlette middleware in the literal sense (deliberately not applied
globally) -- rate limiting only makes sense on the endpoints that
actually cost money/resources (AI Gateway calls), not on every request
(health checks, auth, DB-only dashboard reads shouldn't be limited the
same way)."""
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.ratelimit.limiter import get_rate_limiter


async def enforce_rate_limit(tenant_id: str, user_id: str, scope: str) -> JSONResponse | None:
    """Returns a 429 JSONResponse if the caller is over their limit for
    this scope (e.g. "department_invoke", "workflow_run"), or None if
    the request is allowed to proceed. Rate-limit key is tenant+user+scope,
    so one user hammering one endpoint doesn't affect their own or anyone
    else's budget for a different endpoint."""
    settings = get_settings()
    limiter = get_rate_limiter()
    key = f"{tenant_id}:{user_id}:{scope}"

    allowed, remaining = await limiter.check(key, settings.RATE_LIMIT_REQUESTS_PER_MINUTE, window_seconds=60)
    if not allowed:
        return JSONResponse(
            {
                "detail": f"Rate limit exceeded: {settings.RATE_LIMIT_REQUESTS_PER_MINUTE} requests per minute for this action.",
            },
            status_code=429,
            headers={"Retry-After": "60"},
        )
    return None
