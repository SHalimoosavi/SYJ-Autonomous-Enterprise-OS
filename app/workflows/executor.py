"""
Shared step-execution loop for a WorkflowRun that already exists in the
DB. Used two ways:
  - app/workflows/router.py's run_workflow(): awaited directly, in the
    same event loop as the HTTP request (the default, Termux-safe path).
  - app/workflows/tasks.py's Celery task: run via asyncio.run() inside a
    separate worker process (the production-only async path -- see that
    module's docstring for why this split exists).

Both call the exact same function against the exact same tables, so a
run started synchronously and one started via Celery are indistinguishable
once you're just looking at WorkflowRun/WorkflowStepRun rows.
"""
from datetime import datetime, timezone

from app.ai_gateway.gateway import AllProvidersFailedError, get_gateway
from app.audit.service import record_audit
from app.core.database import AsyncSessionLocal, set_tenant_context
from app.departments.registry import get_agent
from app.workflows.models import RunStatus, WorkflowRun, WorkflowStepRun
from app.workflows.registry import get_workflow


class WorkflowRunNotFound(Exception):
    pass


async def execute_workflow_run(run_id: str, tenant_id: str, actor_id: str, workflow_slug: str, input_prompt: str) -> dict:
    """
    Executes every step of `workflow_slug` against the already-created
    WorkflowRun row `run_id`, writing a WorkflowStepRun per step and
    updating the run's final status. Returns a result dict describing the
    outcome -- the synchronous route handler uses this to build its
    immediate HTTP response; the Celery task (async path) ignores the
    return value entirely and lets callers read the outcome back from the
    DB via GET /api/v1/workflows/runs/{run_id}. Either way, the DB state
    this function leaves behind is identical, which is what makes the
    sync and async paths interchangeable from the caller's point of view.
    """
    from sqlalchemy import select

    workflow = get_workflow(workflow_slug)
    if workflow is None:
        raise WorkflowRunNotFound(f"Unknown workflow: {workflow_slug}")

    gateway = get_gateway()
    previous_output = ""
    step_results = []

    for index, step in enumerate(workflow.steps):
        agent = get_agent(step.department, gateway)
        prompt = step.prompt_template.format(input=input_prompt, previous=previous_output or "(no prior step)")

        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, tenant_id)
            try:
                response = await agent.run(prompt, tenant_id=tenant_id)
            except AllProvidersFailedError as exc:
                session.add(WorkflowStepRun(
                    run_id=run_id, step_index=index, department=step.department,
                    status=RunStatus.FAILED, error=str(exc),
                ))
                run = (await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
                run.status = RunStatus.FAILED
                run.error = f"Step {index} ({step.department}) failed: {exc}"
                run.completed_at = datetime.now(timezone.utc)
                session.add(run)
                await session.commit()
                await record_audit(
                    session, tenant_id, actor_id, "workflow.failed", resource=run_id,
                    metadata={"workflow": workflow_slug, "failed_step": step.department, "error": str(exc)},
                )
                return {
                    "status": "failed", "failed_step": step.department, "error": str(exc),
                    "completed_steps": step_results,
                }

            session.add(WorkflowStepRun(
                run_id=run_id, step_index=index, department=step.department,
                status=RunStatus.COMPLETED, output_text=response.text,
            ))
            await session.commit()

        step_results.append({"department": step.department, "output": response.text})
        previous_output = response.text

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, tenant_id)
        run = (await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
        run.status = RunStatus.COMPLETED
        run.completed_at = datetime.now(timezone.utc)
        session.add(run)
        await session.commit()
        await record_audit(session, tenant_id, actor_id, "workflow.completed", resource=run_id,
                            metadata={"workflow": workflow_slug})

    return {"status": "completed", "steps": step_results}
