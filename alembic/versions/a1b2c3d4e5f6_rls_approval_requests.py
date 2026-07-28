"""extend postgres RLS to approval_requests

Revision ID: a1b2c3d4e5f6
Revises: 5a2e1832ab56
Create Date: 2026-07-28

approval_requests is a new tenant-scoped table introduced in this same
Phase 2 revision; it belongs under the same row-level-security coverage
as users/roles/audit_logs from
f1a2b3c4d5e6_postgres_row_level_security.py. Kept as a separate migration
rather than editing that already-applied one, same no-op-on-SQLite pattern.
"""
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "5a2e1832ab56"
branch_labels = None
depends_on = None

TABLE = "approval_requests"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_{TABLE} ON {TABLE}
        USING (tenant_id = current_setting('app.current_tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{TABLE} ON {TABLE}")
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")
