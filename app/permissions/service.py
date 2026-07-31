"""
Shared role/permission business logic, used by both the JSON API
(app/permissions/router.py) and the HTML admin UI
(app/admin/router.py) -- one implementation of "create a role", "assign
a permission", etc., not two parallel copies that could drift apart.
Callers own the DB session and set_tenant_context() (a session-lifecycle
concern, not business logic); these functions just do the work and raise
NotFound where the JSON router turns that into a 404 and the admin
router turns it into a re-rendered page with an error.
"""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.audit.service import record_audit
from app.auth.models import Permission, Role, User
from app.departments.registry import CAPABILITIES


class NotFound(Exception):
    pass


def is_owner_or_admin(user: User) -> bool:
    return user.is_tenant_owner or user.is_platform_admin


async def ensure_permission_catalog_seeded(session) -> None:
    """Idempotently seeds one Permission row per department's
    required_permission code, so the catalog always reflects what
    app.departments.registry actually enforces. Permission.code is
    globally unique (not per-tenant), so this is safe to call from any
    tenant's request -- the catalog is shared."""
    codes = {cap.required_permission for cap in CAPABILITIES.values() if cap.required_permission}
    existing = {p.code for p in (await session.execute(select(Permission))).scalars().all()}
    missing = codes - existing
    for code in missing:
        session.add(Permission(code=code, description=f"Auto-seeded from department registry: {code}"))
    if missing:
        await session.commit()


async def list_permission_catalog(session) -> list[Permission]:
    await ensure_permission_catalog_seeded(session)
    return (await session.execute(select(Permission).order_by(Permission.code))).scalars().all()


async def list_tenant_roles(session, tenant_id: str) -> list[Role]:
    return (
        await session.execute(
            select(Role).where(Role.tenant_id == tenant_id).options(selectinload(Role.permissions))
        )
    ).scalars().all()


async def create_role(session, tenant_id: str, actor_id: str, name: str) -> Role:
    role = Role(tenant_id=tenant_id, name=name)
    session.add(role)
    await session.commit()
    await session.refresh(role)
    await record_audit(session, tenant_id, actor_id, "role.created", resource=role.id, metadata={"name": name})
    return role


async def assign_permission_to_role(session, tenant_id: str, actor_id: str, role_id: str, permission_code: str) -> Role:
    await ensure_permission_catalog_seeded(session)

    role = (
        await session.execute(
            select(Role).where(Role.id == role_id, Role.tenant_id == tenant_id).options(selectinload(Role.permissions))
        )
    ).scalar_one_or_none()
    if role is None:
        raise NotFound("Role not found")

    permission = (
        await session.execute(select(Permission).where(Permission.code == permission_code))
    ).scalar_one_or_none()
    if permission is None:
        raise NotFound(f"Unknown permission code: {permission_code}")

    if permission not in role.permissions:
        role.permissions.append(permission)
        session.add(role)
        await session.commit()
        await record_audit(
            session, tenant_id, actor_id, "role.permission_assigned",
            resource=role.id, metadata={"permission_code": permission_code},
        )
    return role


async def list_tenant_users(session, tenant_id: str) -> list[User]:
    return (
        await session.execute(
            select(User).where(User.tenant_id == tenant_id).options(selectinload(User.roles))
        )
    ).scalars().all()


async def assign_role_to_user(session, tenant_id: str, actor_id: str, target_user_id: str, role_id: str) -> User:
    target = (
        await session.execute(
            select(User).where(User.id == target_user_id, User.tenant_id == tenant_id).options(selectinload(User.roles))
        )
    ).scalar_one_or_none()
    if target is None:
        raise NotFound("User not found")

    role = (
        await session.execute(select(Role).where(Role.id == role_id, Role.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if role is None:
        raise NotFound("Role not found")

    if role not in target.roles:
        target.roles.append(role)
        session.add(target)
        await session.commit()
        await record_audit(
            session, tenant_id, actor_id, "user.role_assigned",
            resource=target.id, metadata={"role_id": role_id, "role_name": role.name},
        )
    return target
