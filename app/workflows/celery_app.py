"""
Celery application for async workflow execution -- production-only, same
as Redis and the async event bus (see docs/TERMUX.md). Importing this
module does NOT require Celery/Redis to be installed for the rest of the
app to work: it's only imported by app/workflows/tasks.py, which is
itself only imported lazily inside router.py's async-mode branch, so a
Termux install that never sets WORKFLOW_ASYNC_ENABLED=true never touches
this file at all.
"""
from celery import Celery

from app.core.config import get_settings

# A Celery worker process only imports what its tasks directly need
# (app.workflows.executor -> app.workflows.models, app.departments.registry,
# etc.) -- unlike the main app process, where main.py transitively imports
# every router and therefore every model. SQLAlchemy resolves ForeignKey
# targets (e.g. WorkflowRun.tenant_id -> "tenants.id") lazily against
# Base.metadata the first time a mapper is used, so if Tenant/User were
# never imported in *this* process, that resolution fails with
# NoReferencedTableError the moment a task actually touches the DB --
# a real bug caught by live-testing this against an actual worker process,
# not by unit tests that only ever run inside the main app process. Same
# fix as alembic/env.py: explicitly import every model module so the
# metadata is complete regardless of which routers happen to be absent
# from this process.
from app.tenancy.models import Tenant  # noqa: F401
from app.auth.models import User, Role, Permission  # noqa: F401
from app.audit.logger import AuditLog  # noqa: F401
from app.approvals.models import ApprovalRequest  # noqa: F401
from app.knowledge.models import KnowledgeChunk  # noqa: F401
from app.dashboard.models import KPIMetric, SalesDeal, FinancialTransaction  # noqa: F401

settings = get_settings()

celery_app = Celery("saeos_workflows", broker=settings.CELERY_BROKER_URL, backend=settings.CELERY_BROKER_URL)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
