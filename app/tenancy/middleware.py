"""
Resolves the active tenant for every request from header, subdomain, or
(for the browser-based admin UI) a cookie, and binds it to request
state. Every downstream DB query filters on this tenant_id — this is the
single choke point that guarantees isolation.
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

        # Fallback: a browser navigating the admin UI can't set custom
        # headers on a plain GET, so /admin/login sets this cookie
        # alongside the session cookie (see app/admin/router.py). Only
        # consulted when header/subdomain gave nothing -- API clients are
        # completely unaffected by this cookie even if one happens to be
        # present (e.g. a browser calling the JSON API directly).
        if not tenant_id:
            tenant_id = request.cookies.get("saeos_tenant")

        if not tenant_id and not request.url.path.startswith(
            ("/health", "/docs", "/openapi.json", "/api/v1/auth/register", "/admin")
        ):
            return JSONResponse(status_code=400, content={"detail": "Missing tenant context"})

        request.state.tenant_id = tenant_id
        return await call_next(request)
