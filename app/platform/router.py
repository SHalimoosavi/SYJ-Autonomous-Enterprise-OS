"""
Platform-admin cross-tenant view. `is_platform_admin` has existed on the
User model since Phase 1.1 with no dedicated surface of its own until
now -- these are the endpoints that actually use it for something.

Every handler authenticates the caller normally (they still need a valid
token for their own home tenant -- see app/auth/rbac.py's
get_current_user, unchanged), then checks user.is_platform_admin before
doing anything cross-tenant. The queries themselves deliberately omit a
tenant_id filter (that's the whole point), and call
set_platform_admin_context() so the equivalent Postgres RLS bypass is
also engaged -- both layers agreeing this specific request is legitimate
cross-tenant access, not just one.
"""
from sqlalchemy import func, select
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.approvals.models import ApprovalRequest, ApprovalStatus
from app.audit.logger import AuditLog
from app.auth.models import User
from app.auth.rbac import Unauthorized, get_current_user
from app.core.database import AsyncSessionLocal, set_platform_admin_context
from app.tenancy.models import Tenant


def _require_platform_admin(user: User) -> JSONResponse | None:
    if not user.is_platform_admin:
        return JSONResponse({"detail": "Platform admin access required"}, status_code=403)
    return None


async def list_tenants(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _require_platform_admin(user)) is not None:
        return denied

    async with AsyncSessionLocal() as session:
        await set_platform_admin_context(session)
        tenants = (await session.execute(select(Tenant).order_by(Tenant.created_at.desc()))).scalars().all()

    return JSONResponse({
        "tenants": [
            {
                "id": t.id, "name": t.name, "slug": t.slug, "plan": t.plan.value, "status": t.status.value,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tenants
        ]
    })


async def tenant_detail(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _require_platform_admin(user)) is not None:
        return denied

    target_tenant_id = request.path_params["tenant_id"]

    async with AsyncSessionLocal() as session:
        await set_platform_admin_context(session)

        tenant = (await session.execute(select(Tenant).where(Tenant.id == target_tenant_id))).scalar_one_or_none()
        if tenant is None:
            return JSONResponse({"detail": "Tenant not found"}, status_code=404)

        user_count = (await session.execute(
            select(func.count()).select_from(User).where(User.tenant_id == target_tenant_id)
        )).scalar_one()
        pending_approvals = (await session.execute(
            select(func.count()).select_from(ApprovalRequest).where(
                ApprovalRequest.tenant_id == target_tenant_id, ApprovalRequest.status == ApprovalStatus.PENDING
            )
        )).scalar_one()
        audit_events = (await session.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == target_tenant_id)
        )).scalar_one()

    return JSONResponse({
        "id": tenant.id, "name": tenant.name, "slug": tenant.slug,
        "plan": tenant.plan.value, "status": tenant.status.value,
        "user_count": user_count, "pending_approvals": pending_approvals, "total_audit_events": audit_events,
    })


async def platform_stats(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _require_platform_admin(user)) is not None:
        return denied

    async with AsyncSessionLocal() as session:
        await set_platform_admin_context(session)

        tenant_count = (await session.execute(select(func.count()).select_from(Tenant))).scalar_one()
        user_count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
        audit_event_count = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
        pending_approval_count = (await session.execute(
            select(func.count()).select_from(ApprovalRequest).where(ApprovalRequest.status == ApprovalStatus.PENDING)
        )).scalar_one()

    return JSONResponse({
        "total_tenants": tenant_count,
        "total_users": user_count,
        "total_audit_events": audit_event_count,
        "total_pending_approvals": pending_approval_count,
    })


routes = [
    Route("/api/v1/platform/tenants", list_tenants),
    Route("/api/v1/platform/tenants/{tenant_id}", tenant_detail),
    Route("/api/v1/platform/stats", platform_stats),
]
