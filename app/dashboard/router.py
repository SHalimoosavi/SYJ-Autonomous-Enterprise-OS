"""
Dashboard API surface. Phase 2 scope is deliberately limited to what has
real data behind it: the Approval Queue and the audit log. KPIs, sales
pipeline, financial summary, etc. from the master spec's Dashboard section
need actual department data sources that don't exist yet -- Phase 3.
"""
from sqlalchemy import func, select
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.ai_gateway.gateway import AllProvidersFailedError, get_gateway
from app.approvals.models import ApprovalRequest, ApprovalStatus
from app.audit.logger import AuditLog
from app.auth.rbac import Unauthorized, get_current_user
from app.core.database import AsyncSessionLocal, set_tenant_context
from app.departments.registry import get_agent


async def ceo_briefing(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    if not (user.is_tenant_owner or user.is_platform_admin):
        return JSONResponse({"detail": "The CEO briefing is only available to the tenant owner"}, status_code=403)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)

        pending_count = (
            await session.execute(
                select(func.count()).select_from(ApprovalRequest).where(
                    ApprovalRequest.tenant_id == user.tenant_id,
                    ApprovalRequest.status == ApprovalStatus.PENDING,
                )
            )
        ).scalar_one()

        recent_approvals = (
            await session.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.tenant_id == user.tenant_id, ApprovalRequest.status == ApprovalStatus.PENDING)
                .order_by(ApprovalRequest.created_at.desc())
                .limit(10)
            )
        ).scalars().all()

        recent_activity_count = (
            await session.execute(
                select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == user.tenant_id)
            )
        ).scalar_one()

    briefing = {
        "pending_approvals": pending_count,
        "pending_approval_titles": [a.title for a in recent_approvals],
        "total_audit_events": recent_activity_count,
        "ai_synthesis": None,
        "ai_synthesis_error": None,
    }

    # Best-effort AI synthesis on top of the real numbers above -- if no
    # provider is available (no keys configured, Ollama not running),
    # the briefing still returns with the real data and a clear note
    # instead of failing the whole request.
    agent = get_agent("executive_office", get_gateway())
    summary_input = (
        f"Pending approvals: {pending_count} ({', '.join(briefing['pending_approval_titles']) or 'none'}). "
        f"Total audit events on record: {recent_activity_count}. "
        "Write a 2-3 sentence CEO briefing from this."
    )
    try:
        response = await agent.run(summary_input, tenant_id=user.tenant_id)
        briefing["ai_synthesis"] = response.text
    except AllProvidersFailedError as exc:
        briefing["ai_synthesis_error"] = (
            "No AI provider is currently available to synthesize a narrative briefing; "
            "the figures above are still accurate."
        )

    return JSONResponse(briefing)


routes = [
    Route("/api/v1/dashboard/briefing", ceo_briefing),
]
