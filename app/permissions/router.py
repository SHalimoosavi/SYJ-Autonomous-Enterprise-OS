"""
Role/Permission management JSON API. All endpoints restricted to the
tenant owner or a platform admin -- administrative surface, not
something regular staff users touch. Thin wrappers over
app/permissions/service.py, which is also used by the HTML admin UI
(app/admin/router.py) -- see that service module's docstring.
"""
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.auth.rbac import Unauthorized, get_current_user
from app.core.database import AsyncSessionLocal, set_tenant_context
from app.permissions import service


def _require_owner_or_admin(user) -> JSONResponse | None:
    if not service.is_owner_or_admin(user):
        return JSONResponse({"detail": "Only the tenant owner can manage roles and permissions"}, status_code=403)
    return None


async def list_permissions(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _require_owner_or_admin(user)) is not None:
        return denied

    async with AsyncSessionLocal() as session:
        perms = await service.list_permission_catalog(session)

    return JSONResponse({"permissions": [{"code": p.code, "description": p.description} for p in perms]})


async def list_roles(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _require_owner_or_admin(user)) is not None:
        return denied

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        roles = await service.list_tenant_roles(session, user.tenant_id)

    return JSONResponse(
        {"roles": [{"id": r.id, "name": r.name, "permissions": [p.code for p in r.permissions]} for r in roles]}
    )


async def create_role(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _require_owner_or_admin(user)) is not None:
        return denied

    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    name = (body or {}).get("name", "").strip()
    if not name:
        return JSONResponse({"detail": "name is required"}, status_code=400)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        role = await service.create_role(session, user.tenant_id, user.id, name)

    return JSONResponse({"id": role.id, "name": role.name}, status_code=201)


async def assign_permission_to_role(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _require_owner_or_admin(user)) is not None:
        return denied

    role_id = request.path_params["role_id"]
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    permission_code = (body or {}).get("permission_code", "").strip()
    if not permission_code:
        return JSONResponse({"detail": "permission_code is required"}, status_code=400)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        try:
            await service.assign_permission_to_role(session, user.tenant_id, user.id, role_id, permission_code)
        except service.NotFound as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)

    return JSONResponse({"role_id": role_id, "permission_code": permission_code})


async def list_users(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _require_owner_or_admin(user)) is not None:
        return denied

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        users = await service.list_tenant_users(session, user.tenant_id)

    return JSONResponse(
        {
            "users": [
                {"id": u.id, "email": u.email, "is_tenant_owner": u.is_tenant_owner, "roles": [r.name for r in u.roles]}
                for u in users
            ]
        }
    )


async def assign_role_to_user(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _require_owner_or_admin(user)) is not None:
        return denied

    target_user_id = request.path_params["user_id"]
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    role_id = (body or {}).get("role_id", "").strip()
    if not role_id:
        return JSONResponse({"detail": "role_id is required"}, status_code=400)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        try:
            await service.assign_role_to_user(session, user.tenant_id, user.id, target_user_id, role_id)
        except service.NotFound as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)

    return JSONResponse({"user_id": target_user_id, "role_id": role_id})


routes = [
    Route("/api/v1/permissions", list_permissions),
    Route("/api/v1/roles", list_roles),
    Route("/api/v1/roles", create_role, methods=["POST"]),
    Route("/api/v1/roles/{role_id}/permissions", assign_permission_to_role, methods=["POST"]),
    Route("/api/v1/users", list_users),
    Route("/api/v1/users/{user_id}/roles", assign_role_to_user, methods=["POST"]),
]
