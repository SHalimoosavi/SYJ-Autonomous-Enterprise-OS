"""extend postgres RLS to knowledge_chunks and workflow_runs

Revision ID: b2c3d4e5f6a7
Revises: 0fd9e132049a
Create Date: 2026-07-28

Same no-op-on-SQLite pattern as the earlier RLS migrations. Note:
workflow_step_runs is intentionally NOT given its own policy -- it has no
tenant_id column (by design, to avoid denormalizing it onto every step
row); it's reached only through workflow_runs.id in application code, and
inherits tenant isolation transitively from that FK relationship the same
way role_permissions/user_roles do from roles/users.
"""
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "0fd9e132049a"
branch_labels = None
depends_on = None

TABLES = ["knowledge_chunks", "workflow_runs"]


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
