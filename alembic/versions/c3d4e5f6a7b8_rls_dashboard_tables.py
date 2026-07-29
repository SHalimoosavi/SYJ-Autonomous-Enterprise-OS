"""extend postgres RLS to dashboard tables

Revision ID: c3d4e5f6a7b8
Revises: a5cc63ff556f
Create Date: 2026-07-29

Same no-op-on-SQLite pattern as every earlier RLS migration.
"""
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "a5cc63ff556f"
branch_labels = None
depends_on = None

TABLES = ["kpi_metrics", "sales_deals", "financial_transactions"]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
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
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
