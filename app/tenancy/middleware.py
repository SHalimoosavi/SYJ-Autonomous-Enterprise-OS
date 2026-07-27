"""
Resolves the active tenant for every request from header or subdomain,
and binds it to request state. Every downstream DB query filters on this
tenant_id — this is the single choke point that guarantees isolation.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import get_settings

settings = get_settings()


class TenantContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        tenant_id = request.headers.get(settings.DEFAULT_TENANT_HEADER)

        # Fallback: resolve from subdomain, e.g. acme.saeos.app -> "acme"
        # (skip bare IP:port hosts like "127.0.0.1:8000", used in local dev/
        # tests, which would otherwise be misparsed as a 4-label subdomain)
        if not tenant_id:
            host = request.headers.get("host", "").split(":")[0]
            parts = host.split(".")
            is_ip_literal = all(p.isdigit() for p in parts)
            if len(parts) > 2 and not is_ip_literal:
                tenant_id = parts[0]

        if not tenant_id and not request.url.path.startswith(
            ("/health", "/docs", "/openapi.json", "/api/v1/auth/register")
        ):
            return JSONResponse(status_code=400, content={"detail": "Missing tenant context"})

        request.state.tenant_id = tenant_id
        return await call_next(request)
