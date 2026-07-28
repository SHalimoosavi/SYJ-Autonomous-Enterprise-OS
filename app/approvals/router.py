"""
Approval Queue endpoints. Deciding an approval (approve/reject) is
deliberately restricted to the tenant owner or a platform admin --
matches the master spec's Human Role: "The CEO approves strategic
decisions, financial commitments, legal matters, and exceptions." A
regular staff user can list and create requests, but not decide them.
"""
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.approvals.models import ApprovalRequest, ApprovalStatus
from app.audit.service import record_audit
from app.auth.rbac import Unauthorized, get_current_user
from app.core.database import AsyncSessionLocal, set_tenant_context


async def create_approval(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    title = (body or {}).get("title", "").strip()
    department = (body or {}).get("department", "").strip() or "general"
    description = (body or {}).get("description", "").strip()

    if not title:
        return JSONResponse({"detail": "title is required"}, status_code=400)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        approval = ApprovalRequest(
            tenant_id=user.tenant_id,
            department=department,
            title=title,
            description=description,
            requested_by=user.id,
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)
        await record_audit(session, user.tenant_id, user.id, "approval.created", resource=approval.id)

    return JSONResponse(
        {"id": approval.id, "title": approval.title, "status": approval.status.value}, status_code=201
    )


async def list_approvals(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    status_filter = request.query_params.get("status")

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        stmt = select(ApprovalRequest).where(ApprovalRequest.tenant_id == user.tenant_id)
        if status_filter:
            try:
                stmt = stmt.where(ApprovalRequest.status == ApprovalStatus(status_filter))
            except ValueError:
                return JSONResponse({"detail": f"Invalid status: {status_filter}"}, status_code=400)
        stmt = stmt.order_by(ApprovalRequest.created_at.desc())
        results = (await session.execute(stmt)).scalars().all()

    return JSONResponse(
        {
            "approvals": [
                {
                    "id": a.id,
                    "title": a.title,
                    "department": a.department,
                    "status": a.status.value,
                    "requested_by": a.requested_by,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in results
            ]
        }
    )


async def decide_approval(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    if not (user.is_tenant_owner or user.is_platform_admin):
        return JSONResponse({"detail": "Only the tenant owner can decide approvals"}, status_code=403)

    approval_id = request.path_params["approval_id"]
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    decision = (body or {}).get("decision", "").strip().lower()
    if decision not in ("approved", "rejected"):
        return JSONResponse({"detail": "decision must be 'approved' or 'rejected'"}, status_code=400)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        result = await session.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.id == approval_id, ApprovalRequest.tenant_id == user.tenant_id
            )
        )
        approval = result.scalar_one_or_none()
        if approval is None:
            return JSONResponse({"detail": "Approval not found"}, status_code=404)
        if approval.status != ApprovalStatus.PENDING:
            return JSONResponse({"detail": f"Approval already {approval.status.value}"}, status_code=409)

        approval.status = ApprovalStatus(decision)
        approval.decided_by = user.id
        from datetime import datetime, timezone
        approval.decided_at = datetime.now(timezone.utc)
        session.add(approval)
        await session.commit()

        await record_audit(
            session, user.tenant_id, user.id, f"approval.{decision}", resource=approval.id
        )

    return JSONResponse({"id": approval.id, "status": approval.status.value})


routes = [
    Route("/api/v1/approvals", create_approval, methods=["POST"]),
    Route("/api/v1/approvals", list_approvals, methods=["GET"]),
    Route("/api/v1/approvals/{approval_id}/decide", decide_approval, methods=["POST"]),
]
