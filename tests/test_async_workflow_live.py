"""
Live integration test for async (Celery-backed) workflow execution.
Skipped automatically unless CELERY_LIVE_TEST=true -- like
test_pgvector_live.py, this requires real infrastructure (a running
Redis broker AND a running Celery worker process consuming
app.workflows.tasks.execute_workflow_task) that CI/Termux can't be
assumed to have, and is NOT part of the default `pytest` suite.

This exact test was run manually against a real `celery -A
app.workflows.tasks worker` process during Phase 4 development and
caught two real bugs before being written down here:
  1. The worker must be started as `-A app.workflows.tasks`, not
     `-A app.workflows.celery_app` -- pointing at the app module alone
     never imports the module that registers the task, so the worker
     starts successfully but can't execute anything sent to it.
  2. The worker process, unlike the main app process, doesn't
     transitively import every model module -- so without the explicit
     imports added to celery_app.py, SQLAlchemy couldn't resolve the
     tenants/users foreign keys the moment a task touched the DB.

To run: start Redis, start `celery -A app.workflows.tasks worker
--pool=solo`, then:
    CELERY_LIVE_TEST=true WORKFLOW_ASYNC_ENABLED=true \
    CELERY_BROKER_URL=redis://localhost:6379/1 pytest tests/test_async_workflow_live.py -v
"""
import asyncio
import os
import time

import pytest

RUN_LIVE = os.environ.get("CELERY_LIVE_TEST") == "true"

pytestmark = pytest.mark.skipif(not RUN_LIVE, reason="CELERY_LIVE_TEST not set -- skipping live Celery/Redis test")


@pytest.mark.asyncio
async def test_async_workflow_executes_via_real_celery_worker():
    from app.core.database import AsyncSessionLocal, Base, engine
    from app.tenancy.models import Tenant, TenantPlan, TenantStatus
    from app.auth.models import User
    from app.core.security import create_access_token, hash_password
    from app.workflows.tasks import execute_workflow_task
    from app.workflows.models import WorkflowRun, RunStatus
    from sqlalchemy import select

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        tenant = Tenant(name="Celery Live Test", slug="celery-live-test",
                         plan=TenantPlan.SUBSCRIPTION_STARTER, status=TenantStatus.TRIAL)
        session.add(tenant)
        await session.flush()
        user = User(tenant_id=tenant.id, email="owner@celery-live.test",
                    hashed_password=hash_password("testpassword1"),
                    is_platform_admin=False, is_tenant_owner=True)
        session.add(user)
        await session.flush()

        run = WorkflowRun(tenant_id=tenant.id, workflow_slug="release_review", started_by=user.id,
                           status=RunStatus.RUNNING, input_prompt="Add rate limiting")
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id, tenant_id, user_id = run.id, tenant.id, user.id

    # Real Celery dispatch over the real Redis broker -- not a mock.
    execute_workflow_task.delay(run_id, tenant_id, user_id, "release_review", "Add rate limiting")

    # Poll the DB directly (proving the worker, a separate OS process,
    # actually wrote there) rather than going through the HTTP API, to
    # keep this test focused on the Celery integration itself.
    final_status = None
    for _ in range(20):
        await asyncio.sleep(0.5)
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            current = result.scalar_one()
            if current.status != RunStatus.RUNNING:
                final_status = current
                break

    assert final_status is not None, "Workflow run never left RUNNING status -- is the Celery worker running?"
    # No AI provider is configured in this test environment, so the
    # expected, correct outcome is a graceful failure -- proving the
    # worker executed real application logic, not just that it woke up.
    assert final_status.status == RunStatus.FAILED
    assert "engineering" in final_status.error
