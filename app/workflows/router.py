"""
Workflow execution: synchronous (default) or async via Celery+Redis
(opt-in, production only).

The synchronous path executes each step in order within the HTTP request
itself and returns the full result (or the failure point) in the
response -- the default for Termux/dev, requiring nothing beyond what's
already in requirements.txt.

The async path (`"async": true` in the request body) creates the
WorkflowRun row, enqueues a Celery task, and returns 202 immediately with
the run_id for polling via GET /api/v1/workflows/runs/{run_id}. This
requires Celery+Redis to actually be running and
WORKFLOW_ASYNC_ENABLED=true in settings -- see docs/TERMUX.md for why
these aren't part of the default Termux install, and
app/workflows/executor.py's docstring for why both paths write to the
identical WorkflowRun/WorkflowStepRun schema.
"""
from datetime import datetime, timezone

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.ai_gateway.gateway import get_gateway
from app.auth.rbac import PermissionDenied, Unauthorized, get_current_user, require_permission
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, set_tenant_context
from app.departments.registry import get_agent
from app.workflows.executor import execute_workflow_run
from app.workflows.models import RunStatus, WorkflowRun
from app.workflows.registry import get_workflow, list_workflows
from app.ratelimit.middleware import enforce_rate_limit


async def list_workflows_route(request: Request):
    return JSONResponse({"workflows": list_workflows()})


async def run_workflow(request: Request):
    slug = request.path_params["workflow_slug"]
    workflow = get_workflow(slug)
    if workflow is None:
        return JSONResponse({"detail": f"Unknown workflow: {slug}"}, status_code=404)

    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    # A workflow may touch departments the caller isn't individually
    # permitted for; require the permission for every step's department
    # up front, before running (and persisting) anything.
    gateway = get_gateway()
    for step in workflow.steps:
        agent = get_agent(step.department, gateway)
        if agent is None:
            return JSONResponse({"detail": f"Workflow references unknown department: {step.department}"}, status_code=500)
        try:
            require_permission(user, agent.capability.required_permission)
        except PermissionDenied as exc:
            return JSONResponse(
                {"detail": f"Missing permission for step '{step.department}': {exc.permission_code}"},
                status_code=403,
            )

    if (rate_limited := await enforce_rate_limit(user.tenant_id, user.id, "workflow_run")) is not None:
        return rate_limited

    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    input_prompt = (body or {}).get("input", "").strip()
    run_async = bool((body or {}).get("async", False))
    if not input_prompt:
        return JSONResponse({"detail": "input is required"}, status_code=400)

    if run_async:
        settings = get_settings()
        if not settings.WORKFLOW_ASYNC_ENABLED:
            return JSONResponse(
                {"detail": "Async workflow execution is not enabled (set WORKFLOW_ASYNC_ENABLED=true "
                            "and run a Celery worker; see docs/TERMUX.md). Falling back to sync execution "
                            "is not automatic -- retry without \"async\": true, or enable it."},
                status_code=503,
            )

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        run = WorkflowRun(
            tenant_id=user.tenant_id, workflow_slug=slug, started_by=user.id,
            status=RunStatus.RUNNING, input_prompt=input_prompt,
        )
        session.add(run)
        await session.commit()
        # No refresh(): run.id is a client-side UUID default; the
        # response below doesn't use created_at.

    if run_async:
        # Imported lazily: only touches Celery when async mode is actually
        # requested and enabled, so a default Termux install never needs
        # Celery installed at all (see tasks.py's module docstring).
        from app.workflows.tasks import execute_workflow_task
        execute_workflow_task.delay(run.id, user.tenant_id, user.id, slug, input_prompt)
        return JSONResponse({"run_id": run.id, "status": "queued"}, status_code=202)

    result = await execute_workflow_run(run.id, user.tenant_id, user.id, slug, input_prompt)
    status_code = {"completed": 200, "escalated": 202}.get(result["status"], 503)
    return JSONResponse({"run_id": run.id, **result}, status_code=status_code)


async def get_run(request: Request):
    from sqlalchemy import select
    from app.workflows.models import WorkflowStepRun

    run_id = request.path_params["run_id"]
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        run = (
            await session.execute(
                select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.tenant_id == user.tenant_id)
            )
        ).scalar_one_or_none()
        if run is None:
            return JSONResponse({"detail": "Workflow run not found"}, status_code=404)

        steps = (
            await session.execute(
                select(WorkflowStepRun).where(WorkflowStepRun.run_id == run.id).order_by(WorkflowStepRun.step_index)
            )
        ).scalars().all()

    return JSONResponse(
        {
            "run_id": run.id,
            "workflow_slug": run.workflow_slug,
            "status": run.status.value,
            "error": run.error,
            "pending_approval_id": run.pending_approval_id,
            "steps": [
                {
                    "department": s.department, "status": s.status.value, "output": s.output_text,
                    "error": s.error, "escalated": s.escalated, "escalation_reason": s.escalation_reason,
                }
                for s in steps
            ],
        }
    )


async def resume_workflow(request: Request):
    from sqlalchemy import select
    from app.approvals.models import ApprovalRequest, ApprovalStatus
    from app.workflows.executor import run_workflow_from_step
    from app.workflows.models import WorkflowStepRun

    run_id = request.path_params["run_id"]
    try:
        user = await get_current_user(request)
    except Unauthorized as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        run = (
            await session.execute(
                select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.tenant_id == user.tenant_id)
            )
        ).scalar_one_or_none()
        if run is None:
            return JSONResponse({"detail": "Workflow run not found"}, status_code=404)
        if run.status != RunStatus.ESCALATED:
            return JSONResponse(
                {"detail": f"Workflow run is not awaiting approval (status: {run.status.value})"}, status_code=409
            )

        approval = (
            await session.execute(select(ApprovalRequest).where(ApprovalRequest.id == run.pending_approval_id))
        ).scalar_one_or_none()
        if approval is None or approval.status != ApprovalStatus.APPROVED:
            current = approval.status.value if approval else "unknown"
            return JSONResponse(
                {"detail": f"Cannot resume: the linked approval is not approved yet (status: {current})"},
                status_code=409,
            )

        completed_steps = (
            await session.execute(
                select(WorkflowStepRun).where(WorkflowStepRun.run_id == run.id).order_by(WorkflowStepRun.step_index)
            )
        ).scalars().all()
        next_index = len(completed_steps)  # every prior step (including the escalated one) is already recorded
        previous_output = completed_steps[-1].output_text if completed_steps else ""

    if (rate_limited := await enforce_rate_limit(user.tenant_id, user.id, "workflow_run")) is not None:
        return rate_limited

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        run = (await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
        run.status = RunStatus.RUNNING
        session.add(run)
        await session.commit()

    result = await run_workflow_from_step(
        run.id, user.tenant_id, user.id, run.workflow_slug, run.input_prompt,
        start_index=next_index, previous_output=previous_output or "",
    )
    status_code = {"completed": 200, "escalated": 202}.get(result["status"], 503)
    return JSONResponse({"run_id": run.id, **result}, status_code=status_code)


routes = [
    Route("/api/v1/workflows", list_workflows_route),
    Route("/api/v1/workflows/{workflow_slug}/run", run_workflow, methods=["POST"]),
    Route("/api/v1/workflows/runs/{run_id}", get_run),
    Route("/api/v1/workflows/runs/{run_id}/resume", resume_workflow, methods=["POST"]),
]
