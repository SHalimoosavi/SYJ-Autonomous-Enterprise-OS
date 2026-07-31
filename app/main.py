"""
Application entrypoint. Run in Termux with:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Built on Starlette directly rather than FastAPI. FastAPI's package itself
hard-requires pydantic>=2.7 as an install dependency -- so even code that
never imports pydantic still pulls in pydantic-core (Rust/PyO3, no Android
wheel, forces a source build) the moment `pip install fastapi` runs.
Starlette is what FastAPI is built on top of; it depends only on `anyio`
and `typing_extensions`, both pure Python. We lose FastAPI's automatic
OpenAPI docs and Depends()-based DI for now (see app/auth/rbac.py for the
manual equivalent) in exchange for a genuinely Termux-installable stack.
If a deployment target is Linux/cloud only, FastAPI + pydantic v2 can be
layered back in later without touching the department/gateway/tenancy
logic, since none of it imports pydantic.
"""
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.admin.router import routes as admin_routes
from app.api.v1.router import routes as api_v1_routes
from app.core.config import get_settings
from app.tenancy.middleware import TenantContextMiddleware

settings = get_settings()


async def root_health(request):
    return JSONResponse({"status": "ok"})


app = Starlette(
    debug=settings.DEBUG,
    routes=[Route("/health", root_health), *api_v1_routes, *admin_routes],
    middleware=[Middleware(TenantContextMiddleware)],
)
