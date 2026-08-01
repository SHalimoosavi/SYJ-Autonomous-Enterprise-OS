"""platform-admin bypass for all RLS policies

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-31

Same no-op-on-SQLite pattern as every earlier RLS migration.

Every existing RLS policy (users, roles, audit_logs, approval_requests,
knowledge_chunks, workflow_runs, kpi_metrics, sales_deals,
financial_transactions) was written as
`USING (tenant_id = current_setting('app.current_tenant_id', true))`
with FORCE ROW LEVEL SECURITY also applied -- meaning even a Postgres
superuser querying the table directly is subject to the policy. That
was intentional (defense-in-depth against a missing tenant filter
somewhere in application code), but it also means there was no way for
a legitimate platform-admin (is_platform_admin=True, the OS operator)
cross-tenant view to ever see more than one tenant's rows, even with
correct authorization checks in application code -- RLS would silently
filter everything down to zero rows outside their own tenant.

This migration replaces each policy with a version that also allows
access when `app.is_platform_admin` is set to 'true' for the current
transaction (see app.core.database.set_platform_admin_context()) --
which only application code that has already verified
user.is_platform_admin via the normal auth/RBAC path can set. The
tenant-scoped condition remains the default for every other session.
"""
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None

TABLES = [
    "users", "roles", "audit_logs", "approval_requests",
    "knowledge_chunks", "workflow_runs",
    "kpi_metrics", "sales_deals", "financial_transactions",
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING (
                tenant_id = current_setting('app.current_tenant_id', true)
                OR current_setting('app.is_platform_admin', true) = 'true'
            )
            WITH CHECK (
                tenant_id = current_setting('app.current_tenant_id', true)
                OR current_setting('app.is_platform_admin', true) = 'true'
            )
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = current_setting('app.current_tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))
            """
        )
