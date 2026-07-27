"""postgres row-level security policies

Revision ID: f1a2b3c4d5e6
Revises: 988a620599a7
Create Date: 2026-07-28

Row-level security is a Postgres feature with no SQLite equivalent, so
this migration is a deliberate no-op on SQLite (Termux/dev) -- detected
at runtime via the bind's dialect name, not via a separate migration
branch, so `alembic upgrade head` behaves identically on both databases
and Termux dev never needs Postgres-only tooling.

On Postgres (production), this adds RLS policies to every tenant-scoped
table as defense-in-depth *underneath* the application-level tenant
filtering already enforced by TenantContextMiddleware and explicit query
scoping -- so a bug in application code (a missing `.where(tenant_id=...)`
somewhere) still can't leak another tenant's rows, because the database
itself refuses to return them. Policies read the active tenant via
`current_setting('app.current_tenant_id')`, which
`app.core.database.set_tenant_context()` sets per-transaction.
"""
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "988a620599a7"
branch_labels = None
depends_on = None

# Tables with a tenant_id column, in dependency order for downgrade safety.
TENANT_SCOPED_TABLES = ["audit_logs", "roles", "users"]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # no-op on SQLite (Termux/dev)

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")  # applies even to the table owner
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = current_setting('app.current_tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
