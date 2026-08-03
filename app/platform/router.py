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


def _serialize_audit_row(entry: AuditLog) -> dict:
    return {
        "id": entry.id, "tenant_id": entry.tenant_id, "actor_id": entry.actor_id,
        "action": entry.action, "resource": entry.resource, "metadata": entry.metadata_json,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


_MAX_AUDIT_LIMIT = 500


def _parse_audit_limit(request: Request) -> int | JSONResponse:
    raw = request.query_params.get("limit", "100")
    try:
        limit = int(raw)
    except ValueError:
        return JSONResponse({"detail": "limit must be an integer"}, status_code=400)
    if limit < 1 or limit > _MAX_AUDIT_LIMIT:
        return JSONResponse({"detail": f"limit must be between 1 and {_MAX_AUDIT_LIMIT}"}, status_code=400)
    return limit


async def tenant_audit_log(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _require_platform_admin(user)) is not None:
        return denied

    target_tenant_id = request.path_params["tenant_id"]
    limit = _parse_audit_limit(request)
    if isinstance(limit, JSONResponse):
        return limit
    action_filter = request.query_params.get("action")

    async with AsyncSessionLocal() as session:
        await set_platform_admin_context(session)

        tenant = (await session.execute(select(Tenant).where(Tenant.id == target_tenant_id))).scalar_one_or_none()
        if tenant is None:
            return JSONResponse({"detail": "Tenant not found"}, status_code=404)

        stmt = select(AuditLog).where(AuditLog.tenant_id == target_tenant_id)
        if action_filter:
            stmt = stmt.where(AuditLog.action == action_filter)
        stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit)
        entries = (await session.execute(stmt)).scalars().all()

    return JSONResponse({"tenant_id": target_tenant_id, "entries": [_serialize_audit_row(e) for e in entries]})


async def global_audit_log(request: Request):
    """Cross-tenant feed -- the actual point of this endpoint existing
    separately from tenant_audit_log above: a platform operator scanning
    for anomalies across the whole system, not investigating one
    already-known tenant."""
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _require_platform_admin(user)) is not None:
        return denied

    limit = _parse_audit_limit(request)
    if isinstance(limit, JSONResponse):
        return limit
    action_filter = request.query_params.get("action")

    async with AsyncSessionLocal() as session:
        await set_platform_admin_context(session)
        stmt = select(AuditLog)
        if action_filter:
            stmt = stmt.where(AuditLog.action == action_filter)
        stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit)
        entries = (await session.execute(stmt)).scalars().all()

    return JSONResponse({"entries": [_serialize_audit_row(e) for e in entries]})


routes = [
    Route("/api/v1/platform/tenants", list_tenants),
    Route("/api/v1/platform/tenants/{tenant_id}", tenant_detail),
    Route("/api/v1/platform/tenants/{tenant_id}/audit-log", tenant_audit_log),
    Route("/api/v1/platform/audit-log", global_audit_log),
    Route("/api/v1/platform/stats", platform_stats),
]
