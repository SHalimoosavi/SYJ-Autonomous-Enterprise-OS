"""
Versioned REST routes. Plain Starlette Route objects (no FastAPI
APIRouter/Depends) -- see app/main.py for why FastAPI itself is off the
Termux dev path.
"""
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.auth.rbac import protected
from app.auth.router import routes as auth_routes


async def health(request):
    return JSONResponse({"status": "ok", "service": "SAEOS"})


@protected()
async def me(request):
    """Authenticated-only: proves get_current_user resolves a real DB user
    from a bearer token, scoped to the requesting tenant."""
    user = request.state.user
    return JSONResponse(
        {
            "id": user.id,
            "email": user.email,
            "tenant_id": user.tenant_id,
            "is_tenant_owner": user.is_tenant_owner,
        }
    )


@protected(permission_code="executive.view_briefing")
async def executive_briefing_placeholder(request):
    """Authenticated AND authorized: proves require_permission's owner
    bypass + role/permission path both work. Wiring this to a real
    ExecutiveOfficeAgent.run() call (which hits the AI Gateway) is Phase 2 --
    this route exists in Phase 1.1 purely to validate the RBAC gate."""
    return JSONResponse({"status": "authorized", "note": "AI Gateway call wired in Phase 2"})


routes = [
    Route("/api/v1/health", health),
    Route("/api/v1/auth/me", me),
    Route("/api/v1/executive/briefing", executive_briefing_placeholder),
    *auth_routes,
]
