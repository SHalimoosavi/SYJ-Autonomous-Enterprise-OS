"""
Celery task wrapping app.workflows.executor.execute_workflow_run().

A Celery worker process has no existing asyncio event loop (unlike a
request inside the Starlette app), so asyncio.run() here is correct and
safe -- this is the standard pattern for calling async code from a sync
Celery task, not a workaround.

Import this module lazily (only from inside the async-mode branch of
router.py), not at module load time anywhere else -- it imports Celery,
which isn't a default dependency (see requirements.txt).
"""
import asyncio

from app.workflows.celery_app import celery_app
from app.workflows.executor import execute_workflow_run


@celery_app.task(name="saeos.execute_workflow")
def execute_workflow_task(run_id: str, tenant_id: str, actor_id: str, workflow_slug: str, input_prompt: str) -> dict:
    return asyncio.run(execute_workflow_run(run_id, tenant_id, actor_id, workflow_slug, input_prompt))
