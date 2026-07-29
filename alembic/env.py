import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

from app.core.config import get_settings
from app.core.database import Base
from app.tenancy.models import Tenant           # noqa: F401 — registers metadata
from app.auth.models import User, Role, Permission  # noqa: F401
from app.audit.logger import AuditLog           # noqa: F401
from app.approvals.models import ApprovalRequest  # noqa: F401
from app.knowledge.models import KnowledgeChunk  # noqa: F401
from app.workflows.models import WorkflowRun, WorkflowStepRun  # noqa: F401
from app.dashboard.models import KPIMetric, SalesDeal, FinancialTransaction  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata

# CRITICAL: override alembic.ini's static sqlalchemy.url with the app's
# real DATABASE_URL setting (from .env / the environment) every time.
# Without this, `alembic upgrade head` always migrates whatever URL is
# hardcoded in alembic.ini (the SQLite dev default) regardless of what
# DATABASE_URL is actually configured -- silently never touching a
# production Postgres database even when someone thinks they pointed it
# there. This bit both offline and online modes below.
config.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL)


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    connectable = async_engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
