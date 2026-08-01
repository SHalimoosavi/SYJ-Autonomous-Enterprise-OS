"""
Versioned REST routes. Plain Starlette Route objects (no FastAPI
APIRouter/Depends) -- see app/main.py for why FastAPI itself is off the
Termux dev path.
"""
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.auth.rbac import protected
from app.auth.router import routes as auth_routes
from app.api.v1.departments_router import routes as department_routes
from app.approvals.router import routes as approval_routes
from app.dashboard.router import routes as dashboard_routes
from app.knowledge.router import routes as knowledge_routes
from app.workflows.router import routes as workflow_routes
from app.permissions.router import routes as permission_routes
from app.platform.router import routes as platform_routes


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


routes = [
    Route("/api/v1/health", health),
    Route("/api/v1/auth/me", me),
    *auth_routes,
    *department_routes,
    *approval_routes,
    *dashboard_routes,
    *knowledge_routes,
    *workflow_routes,
    *permission_routes,
    *platform_routes,
]
