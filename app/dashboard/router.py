"""
Dashboard API surface. Phase 2/3 added the Approval Queue and audit log
(real from day one); Phase 4 adds the three widgets explicitly deferred
until real data sources existed: KPIs, sales pipeline, financial summary.
All three are plain DB-backed CRUD + aggregation -- no AI Gateway
dependency at all, so unlike most of this project's endpoints these have
zero external-service failure mode to handle gracefully.
"""
from decimal import Decimal

from sqlalchemy import func, select
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.ai_gateway.gateway import AllProvidersFailedError, get_gateway
from app.approvals.models import ApprovalRequest, ApprovalStatus
from app.audit.logger import AuditLog
from app.audit.service import record_audit
from app.auth.rbac import Unauthorized, get_current_user
from app.core.database import AsyncSessionLocal, set_tenant_context
from app.dashboard.models import DealStage, FinancialTransaction, KPIMetric, SalesDeal, TransactionType
from app.departments.registry import get_agent


def _owner_only(user) -> JSONResponse | None:
    if not (user.is_tenant_owner or user.is_platform_admin):
        return JSONResponse({"detail": "Only the tenant owner can access this"}, status_code=403)
    return None


# --- KPIs ---

async def record_kpi(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    department = (body or {}).get("department", "").strip()
    metric_name = (body or {}).get("metric_name", "").strip()
    value = (body or {}).get("value")

    if not department or not metric_name or value is None:
        return JSONResponse({"detail": "department, metric_name, and value are required"}, status_code=400)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return JSONResponse({"detail": "value must be a number"}, status_code=400)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        metric = KPIMetric(tenant_id=user.tenant_id, department=department, metric_name=metric_name, value=value)
        session.add(metric)
        await session.commit()
        await session.refresh(metric)
        await record_audit(session, user.tenant_id, user.id, "kpi.recorded", resource=metric.id,
                            metadata={"metric_name": metric_name, "value": value})

    return JSONResponse({"id": metric.id, "metric_name": metric.metric_name, "value": float(metric.value)}, status_code=201)


async def list_kpis(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    department_filter = request.query_params.get("department")
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        stmt = select(KPIMetric).where(KPIMetric.tenant_id == user.tenant_id)
        if department_filter:
            stmt = stmt.where(KPIMetric.department == department_filter)
        stmt = stmt.order_by(KPIMetric.recorded_at.desc()).limit(100)
        metrics = (await session.execute(stmt)).scalars().all()

    return JSONResponse({
        "metrics": [
            {"id": m.id, "department": m.department, "metric_name": m.metric_name,
             "value": float(m.value), "recorded_at": m.recorded_at.isoformat() if m.recorded_at else None}
            for m in metrics
        ]
    })


# --- Sales pipeline ---

async def create_deal(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    name = (body or {}).get("name", "").strip()
    value = (body or {}).get("value", 0)
    if not name:
        return JSONResponse({"detail": "name is required"}, status_code=400)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return JSONResponse({"detail": "value must be a number"}, status_code=400)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        deal = SalesDeal(tenant_id=user.tenant_id, name=name, value=value, stage=DealStage.LEAD,
                          notes=(body or {}).get("notes", ""))
        session.add(deal)
        await session.commit()
        await session.refresh(deal)
        await record_audit(session, user.tenant_id, user.id, "deal.created", resource=deal.id)

    return JSONResponse({"id": deal.id, "name": deal.name, "stage": deal.stage.value, "value": float(deal.value)}, status_code=201)


async def update_deal_stage(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    deal_id = request.path_params["deal_id"]
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    stage_value = (body or {}).get("stage", "").strip().lower()
    try:
        new_stage = DealStage(stage_value)
    except ValueError:
        return JSONResponse({"detail": f"Invalid stage: {stage_value}"}, status_code=400)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        deal = (await session.execute(
            select(SalesDeal).where(SalesDeal.id == deal_id, SalesDeal.tenant_id == user.tenant_id)
        )).scalar_one_or_none()
        if deal is None:
            return JSONResponse({"detail": "Deal not found"}, status_code=404)
        deal.stage = new_stage
        session.add(deal)
        await session.commit()
        await record_audit(session, user.tenant_id, user.id, "deal.stage_updated", resource=deal.id,
                            metadata={"stage": new_stage.value})

    return JSONResponse({"id": deal.id, "stage": deal.stage.value})


async def list_pipeline(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        deals = (await session.execute(
            select(SalesDeal).where(SalesDeal.tenant_id == user.tenant_id).order_by(SalesDeal.created_at.desc())
        )).scalars().all()

    by_stage: dict[str, dict] = {}
    for d in deals:
        entry = by_stage.setdefault(d.stage.value, {"count": 0, "total_value": 0.0})
        entry["count"] += 1
        entry["total_value"] += float(d.value)

    return JSONResponse({
        "deals": [{"id": d.id, "name": d.name, "stage": d.stage.value, "value": float(d.value)} for d in deals],
        "by_stage": by_stage,
    })


# --- Financial summary ---

async def record_transaction(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _owner_only(user)) is not None:
        return denied

    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    type_value = (body or {}).get("type", "").strip().lower()
    category = (body or {}).get("category", "").strip()
    amount = (body or {}).get("amount")

    try:
        txn_type = TransactionType(type_value)
    except ValueError:
        return JSONResponse({"detail": "type must be 'income' or 'expense'"}, status_code=400)
    if not category or amount is None:
        return JSONResponse({"detail": "category and amount are required"}, status_code=400)
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return JSONResponse({"detail": "amount must be a number"}, status_code=400)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        txn = FinancialTransaction(tenant_id=user.tenant_id, type=txn_type, category=category, amount=amount,
                                    description=(body or {}).get("description", ""))
        session.add(txn)
        await session.commit()
        await session.refresh(txn)
        await record_audit(session, user.tenant_id, user.id, "transaction.recorded", resource=txn.id,
                            metadata={"type": txn_type.value, "amount": amount})

    return JSONResponse({"id": txn.id, "type": txn.type.value, "amount": float(txn.amount)}, status_code=201)


async def financial_summary(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _owner_only(user)) is not None:
        return denied

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        income_total = (await session.execute(
            select(func.coalesce(func.sum(FinancialTransaction.amount), 0)).where(
                FinancialTransaction.tenant_id == user.tenant_id, FinancialTransaction.type == TransactionType.INCOME
            )
        )).scalar_one()
        expense_total = (await session.execute(
            select(func.coalesce(func.sum(FinancialTransaction.amount), 0)).where(
                FinancialTransaction.tenant_id == user.tenant_id, FinancialTransaction.type == TransactionType.EXPENSE
            )
        )).scalar_one()
        recent = (await session.execute(
            select(FinancialTransaction).where(FinancialTransaction.tenant_id == user.tenant_id)
            .order_by(FinancialTransaction.recorded_at.desc()).limit(20)
        )).scalars().all()

    income_total = float(income_total or 0)
    expense_total = float(expense_total or 0)
    return JSONResponse({
        "total_income": income_total,
        "total_expense": expense_total,
        "net": income_total - expense_total,
        "recent_transactions": [
            {"id": t.id, "type": t.type.value, "category": t.category, "amount": float(t.amount)}
            for t in recent
        ],
    })


# --- CEO briefing (enriched with the above) ---

async def ceo_briefing(request: Request):
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    if (denied := _owner_only(user)) is not None:
        return denied

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)

        pending_count = (await session.execute(
            select(func.count()).select_from(ApprovalRequest).where(
                ApprovalRequest.tenant_id == user.tenant_id, ApprovalRequest.status == ApprovalStatus.PENDING
            )
        )).scalar_one()

        recent_approvals = (await session.execute(
            select(ApprovalRequest)
            .where(ApprovalRequest.tenant_id == user.tenant_id, ApprovalRequest.status == ApprovalStatus.PENDING)
            .order_by(ApprovalRequest.created_at.desc()).limit(10)
        )).scalars().all()

        audit_count = (await session.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.tenant_id == user.tenant_id)
        )).scalar_one()

        open_deal_value = (await session.execute(
            select(func.coalesce(func.sum(SalesDeal.value), 0)).where(
                SalesDeal.tenant_id == user.tenant_id,
                SalesDeal.stage.notin_([DealStage.WON, DealStage.LOST]),
            )
        )).scalar_one()

        income_total = (await session.execute(
            select(func.coalesce(func.sum(FinancialTransaction.amount), 0)).where(
                FinancialTransaction.tenant_id == user.tenant_id, FinancialTransaction.type == TransactionType.INCOME
            )
        )).scalar_one()
        expense_total = (await session.execute(
            select(func.coalesce(func.sum(FinancialTransaction.amount), 0)).where(
                FinancialTransaction.tenant_id == user.tenant_id, FinancialTransaction.type == TransactionType.EXPENSE
            )
        )).scalar_one()

    briefing = {
        "pending_approvals": pending_count,
        "pending_approval_titles": [a.title for a in recent_approvals],
        "total_audit_events": audit_count,
        "open_pipeline_value": float(open_deal_value or 0),
        "net_financial_position": float(income_total or 0) - float(expense_total or 0),
        "ai_synthesis": None,
        "ai_synthesis_error": None,
    }

    agent = get_agent("executive_office", get_gateway())
    summary_input = (
        f"Pending approvals: {pending_count} ({', '.join(briefing['pending_approval_titles']) or 'none'}). "
        f"Open sales pipeline value: {briefing['open_pipeline_value']}. "
        f"Net financial position: {briefing['net_financial_position']}. "
        f"Total audit events on record: {audit_count}. "
        "Write a 2-3 sentence CEO briefing from this."
    )
    try:
        response = await agent.run(summary_input, tenant_id=user.tenant_id)
        briefing["ai_synthesis"] = response.text
    except AllProvidersFailedError:
        briefing["ai_synthesis_error"] = (
            "No AI provider is currently available to synthesize a narrative briefing; "
            "the figures above are still accurate."
        )

    return JSONResponse(briefing)


routes = [
    Route("/api/v1/dashboard/briefing", ceo_briefing),
    Route("/api/v1/dashboard/kpis", record_kpi, methods=["POST"]),
    Route("/api/v1/dashboard/kpis", list_kpis),
    Route("/api/v1/dashboard/pipeline", create_deal, methods=["POST"]),
    Route("/api/v1/dashboard/pipeline", list_pipeline),
    Route("/api/v1/dashboard/pipeline/{deal_id}/stage", update_deal_stage, methods=["POST"]),
    Route("/api/v1/dashboard/finance", record_transaction, methods=["POST"]),
    Route("/api/v1/dashboard/finance", financial_summary),
]
