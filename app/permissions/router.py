"""
Role/Permission management. All endpoints here are restricted to the
tenant owner or a platform admin -- this is administrative surface, not
something regular staff users touch. Permission *codes* are the ones
already enforced by app.departments.registry (e.g. "engineering.act")
plus "executive.view_briefing"; this module lets an owner build custom
Roles that bundle a subset of those codes and assign them to specific
users, rather than every non-owner user being denied everything (the
Phase 1.1/2 default) or needing to be made a full tenant owner just to
use one department.
"""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.audit.service import record_audit
from app.auth.models import Permission, Role, User
from app.auth.rbac import Unauthorized, get_current_user
from app.core.database import AsyncSessionLocal, set_tenant_context
from app.departments.registry import CAPABILITIES


def _require_owner_or_admin(user: User) -> JSONResponse | None:
    if not (user.is_tenant_owner or user.is_platform_admin):
        return JSONResponse({"detail": "Only the tenant owner can manage roles and permissions"}, status_code=403)
    return None


async def _ensure_permission_catalog_seeded(session) -> None:
    """Idempotently seeds one Permission row per department's
    required_permission code (plus executive_office's), so the catalog
    always reflects what app.departments.registry actually enforces.
    Permission.code is globally unique (not per-tenant), so this is safe
    to call from any tenant's request -- the catalog is shared."""
    codes = {cap.required_permission for cap in CAPABILITIES.values() if cap.required_permission}
    existing = {p.code for p in (await session.execute(select(Permission))).scalars().all()}
    missing = codes - existing
    for code in missing:
        session.add(Permission(code=code, description=f"Auto-seeded from department registry: {code}"))
    if missing:
        await session.commit()


async def list_permissions(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _require_owner_or_admin(user)) is not None:
        return denied

    async with AsyncSessionLocal() as session:
        await _ensure_permission_catalog_seeded(session)
        perms = (await session.execute(select(Permission).order_by(Permission.code))).scalars().all()

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
        roles = (
            await session.execute(
                select(Role).where(Role.tenant_id == user.tenant_id).options(selectinload(Role.permissions))
            )
        ).scalars().all()

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
        role = Role(tenant_id=user.tenant_id, name=name)
        session.add(role)
        await session.commit()
        await session.refresh(role)
        await record_audit(session, user.tenant_id, user.id, "role.created", resource=role.id, metadata={"name": name})

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
        await _ensure_permission_catalog_seeded(session)

        role = (
            await session.execute(
                select(Role).where(Role.id == role_id, Role.tenant_id == user.tenant_id)
                .options(selectinload(Role.permissions))
            )
        ).scalar_one_or_none()
        if role is None:
            return JSONResponse({"detail": "Role not found"}, status_code=404)

        permission = (
            await session.execute(select(Permission).where(Permission.code == permission_code))
        ).scalar_one_or_none()
        if permission is None:
            return JSONResponse({"detail": f"Unknown permission code: {permission_code}"}, status_code=404)

        if permission not in role.permissions:
            role.permissions.append(permission)
            session.add(role)
            await session.commit()
            await record_audit(
                session, user.tenant_id, user.id, "role.permission_assigned",
                resource=role.id, metadata={"permission_code": permission_code},
            )

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
        users = (
            await session.execute(
                select(User).where(User.tenant_id == user.tenant_id).options(selectinload(User.roles))
            )
        ).scalars().all()

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

        target = (
            await session.execute(
                select(User).where(User.id == target_user_id, User.tenant_id == user.tenant_id)
                .options(selectinload(User.roles))
            )
        ).scalar_one_or_none()
        if target is None:
            return JSONResponse({"detail": "User not found"}, status_code=404)

        role = (
            await session.execute(select(Role).where(Role.id == role_id, Role.tenant_id == user.tenant_id))
        ).scalar_one_or_none()
        if role is None:
            return JSONResponse({"detail": "Role not found"}, status_code=404)

        if role not in target.roles:
            target.roles.append(role)
            session.add(target)
            await session.commit()
            await record_audit(
                session, user.tenant_id, user.id, "user.role_assigned",
                resource=target.id, metadata={"role_id": role_id, "role_name": role.name},
            )

    return JSONResponse({"user_id": target_user_id, "role_id": role_id})


routes = [
    Route("/api/v1/permissions", list_permissions),
    Route("/api/v1/roles", list_roles),
    Route("/api/v1/roles", create_role, methods=["POST"]),
    Route("/api/v1/roles/{role_id}/permissions", assign_permission_to_role, methods=["POST"]),
    Route("/api/v1/users", list_users),
    Route("/api/v1/users/{user_id}/roles", assign_role_to_user, methods=["POST"]),
]
