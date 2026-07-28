"""
Workflow execution: synchronous, step-by-step, within the request.

Deliberate design choice, not a shortcut: a fire-and-forget background
task (asyncio.create_task) that the caller polls for completion is
genuinely hard to verify deterministically against Starlette's sync
TestClient -- the background task's completion isn't guaranteed to
happen before a subsequent poll request in a way that's reliable to
assert on in a test. Rather than ship an orchestration feature whose
correctness can't be fully verified, each step executes and is persisted
in order within the HTTP request itself, and the full result (or the
failure point) comes back in the response. For workloads where this
becomes too slow for a single HTTP request, the production upgrade path
is a Celery task per step writing to these same WorkflowRun /
WorkflowStepRun tables -- the schema doesn't change, only who calls
_execute_step and when.
"""
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.ai_gateway.gateway import AllProvidersFailedError, get_gateway
from app.audit.service import record_audit
from app.auth.rbac import PermissionDenied, Unauthorized, get_current_user, require_permission
from app.core.database import AsyncSessionLocal, set_tenant_context
from app.departments.registry import get_agent
from app.workflows.models import RunStatus, WorkflowRun, WorkflowStepRun
from app.workflows.registry import get_workflow, list_workflows


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

    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    input_prompt = (body or {}).get("input", "").strip()
    if not input_prompt:
        return JSONResponse({"detail": "input is required"}, status_code=400)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        run = WorkflowRun(
            tenant_id=user.tenant_id, workflow_slug=slug, started_by=user.id,
            status=RunStatus.RUNNING, input_prompt=input_prompt,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

    previous_output = ""
    step_results = []

    for index, step in enumerate(workflow.steps):
        agent = get_agent(step.department, gateway)
        prompt = step.prompt_template.format(input=input_prompt, previous=previous_output or "(no prior step)")

        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, user.tenant_id)
            try:
                response = await agent.run(prompt, tenant_id=user.tenant_id)
            except AllProvidersFailedError as exc:
                step_row = WorkflowStepRun(
                    run_id=run.id, step_index=index, department=step.department,
                    status=RunStatus.FAILED, error=str(exc),
                )
                session.add(step_row)
                run.status = RunStatus.FAILED
                run.error = f"Step {index} ({step.department}) failed: {exc}"
                from datetime import datetime, timezone
                run.completed_at = datetime.now(timezone.utc)
                session.add(run)
                await session.commit()
                await record_audit(
                    session, user.tenant_id, user.id, "workflow.failed",
                    resource=run.id, metadata={"workflow": slug, "failed_step": step.department, "error": str(exc)},
                )
                return JSONResponse(
                    {
                        "run_id": run.id, "status": "failed",
                        "failed_step": step.department, "error": str(exc),
                        "completed_steps": step_results,
                    },
                    status_code=503,
                )

            step_row = WorkflowStepRun(
                run_id=run.id, step_index=index, department=step.department,
                status=RunStatus.COMPLETED, output_text=response.text,
            )
            session.add(step_row)
            await session.commit()

        step_results.append({"department": step.department, "output": response.text})
        previous_output = response.text

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, user.tenant_id)
        from datetime import datetime, timezone
        run.status = RunStatus.COMPLETED
        run.completed_at = datetime.now(timezone.utc)
        session.add(run)
        await session.commit()
        await record_audit(session, user.tenant_id, user.id, "workflow.completed", resource=run.id, metadata={"workflow": slug})

    return JSONResponse({"run_id": run.id, "status": "completed", "steps": step_results})


async def get_run(request: Request):
    from sqlalchemy import select

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
            "steps": [
                {"department": s.department, "status": s.status.value, "output": s.output_text, "error": s.error}
                for s in steps
            ],
        }
    )


routes = [
    Route("/api/v1/workflows", list_workflows_route),
    Route("/api/v1/workflows/{workflow_slug}/run", run_workflow, methods=["POST"]),
    Route("/api/v1/workflows/runs/{run_id}", get_run),
]
