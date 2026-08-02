"""
Shared step-execution loop for a WorkflowRun that already exists in the
DB. Used two ways:
  - app/workflows/router.py's run_workflow() and resume_workflow():
    awaited directly, in the same event loop as the HTTP request (the
    default, Termux-safe path).
  - app/workflows/tasks.py's Celery task: run via asyncio.run() inside a
    separate worker process (the production-only async path).

Both call the exact same function against the exact same tables, so a
run started synchronously and one started via Celery are indistinguishable
once you're just looking at WorkflowRun/WorkflowStepRun rows.

Escalation (Phase 7): after each successful step, the department agent's
should_escalate() (same keyword-based check department-invoke uses,
Phase 6) is evaluated against that step's prompt+response. A match
creates a real ApprovalRequest, marks the run ESCALATED, and stops --
deliberately not "flag and continue", since later steps chain the
escalated step's output into their own prompt via {previous}, and
letting that compound without founder sign-off would defeat the point
of escalating in the first place. See run_workflow_from_step()'s
start_index/previous_output parameters, which are what make resuming
after approval possible without re-running already-completed steps.
"""
from datetime import datetime, timezone

from app.ai_gateway.gateway import AllProvidersFailedError, get_gateway
from app.approvals import service as approval_service
from app.audit.service import record_audit
from app.core.database import AsyncSessionLocal, set_tenant_context
from app.departments.registry import get_agent
from app.workflows.models import RunStatus, WorkflowRun, WorkflowStepRun
from app.workflows.registry import get_workflow


class WorkflowRunNotFound(Exception):
    pass


async def execute_workflow_run(run_id: str, tenant_id: str, actor_id: str, workflow_slug: str, input_prompt: str) -> dict:
    """Starts a workflow from the first step. Thin wrapper over
    run_workflow_from_step() -- kept as a separate name since it's the
    far more common call (every fresh `POST .../run`), while
    run_workflow_from_step() exists mainly to make resuming explicit and
    testable in its own right."""
    return await run_workflow_from_step(run_id, tenant_id, actor_id, workflow_slug, input_prompt, start_index=0, previous_output="")


async def run_workflow_from_step(
    run_id: str, tenant_id: str, actor_id: str, workflow_slug: str, input_prompt: str,
    start_index: int, previous_output: str,
) -> dict:
    """
    Executes workflow_slug's steps starting at start_index (0 for a
    fresh run, or the first not-yet-completed step index when resuming
    after an approved escalation), using previous_output as the
    {previous} template value for the first step executed here (the
    prior step's actual output when resuming, or "" for a fresh start).
    """
    from sqlalchemy import select

    workflow = get_workflow(workflow_slug)
    if workflow is None:
        raise WorkflowRunNotFound(f"Unknown workflow: {workflow_slug}")

    gateway = get_gateway()
    step_results = []

    for index in range(start_index, len(workflow.steps)):
        step = workflow.steps[index]
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

            # Escalation check -- same keyword-based should_escalate() as
            # department-invoke (Phase 6), but evaluated against the
            # RESPONSE text only, not prompt+response. Unlike a single
            # department-invoke call (where the prompt is genuinely
            # user-authored free text and can legitimately carry signal),
            # a workflow step's prompt is generated from
            # step.prompt_template -- fixed scaffolding written by this
            # project, not user intent. Several templates ask a
            # department directly about its own risk-relevant function
            # (e.g. DevOps: "what deployment steps..."), which contains
            # trigger keywords like "deploy" unconditionally regardless
            # of the actual response. Including the prompt here would
            # make that step's escalation depend on the workflow
            # definition's wording rather than what the AI actually said
            # -- a real bug caught by an escalation test that should NOT
            # have triggered but did, every time, for exactly that reason.
            matched_rule = agent.should_escalate({"text": response.text})
            if matched_rule is not None:
                approval = await approval_service.create_approval(
                    session, tenant_id, step.department,
                    title=f"[workflow:{workflow_slug} step {index}] {prompt[:120]}",
                    description=(
                        f"AI response:\n{response.text}\n\n"
                        f"Escalation reason: {matched_rule.condition}\n\n"
                        f"This step is part of workflow run {run_id}. Approving this request and calling "
                        f"POST /api/v1/workflows/runs/{run_id}/resume will continue the workflow from the next step."
                    ),
                    requested_by=f"agent:{step.department}",
                )
                session.add(WorkflowStepRun(
                    run_id=run_id, step_index=index, department=step.department,
                    status=RunStatus.COMPLETED, output_text=response.text,
                    escalated=True, escalation_reason=matched_rule.condition,
                ))
                run = (await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalar_one()
                run.status = RunStatus.ESCALATED
                run.pending_approval_id = approval.id
                session.add(run)
                await session.commit()
                await record_audit(
                    session, tenant_id, actor_id, "workflow.escalated", resource=run_id,
                    metadata={"workflow": workflow_slug, "step": step.department,
                              "approval_id": approval.id, "reason": matched_rule.condition},
                )
                step_results.append({"department": step.department, "output": response.text, "escalated": True})
                return {
                    "status": "escalated", "escalated_step": step.department,
                    "approval_id": approval.id, "reason": matched_rule.condition,
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
        run.pending_approval_id = None
        session.add(run)
        await session.commit()
        await record_audit(session, tenant_id, actor_id, "workflow.completed", resource=run_id,
                            metadata={"workflow": workflow_slug})

    return {"status": "completed", "steps": step_results}
