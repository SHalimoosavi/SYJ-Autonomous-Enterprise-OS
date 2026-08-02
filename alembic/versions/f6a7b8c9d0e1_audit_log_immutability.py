"""audit log immutability (postgres trigger)

Revision ID: f6a7b8c9d0e1
Revises: 52011ed7e2e7
Create Date: 2026-08-02

Same no-op-on-SQLite pattern as every RLS migration. Noted as an open
gap since Phase 1's original security review ("nothing here yet
enforces immutability at the DB level -- add a Postgres trigger or
table-level grant restriction in production"); this is that fix.

Uses a BEFORE UPDATE OR DELETE trigger that unconditionally raises,
rather than a REVOKE on a specific role name. A trigger is
role-independent -- it doesn't need to know the application's role name
(saeos_app, or whatever a given deployment calls it) and can't be
bypassed by simply connecting as a different role the way a REVOKE-based
approach could be. The one real cost: even the table owner can't
UPDATE/DELETE through normal SQL anymore either, including for a
legitimate retention-policy purge. That's intentional for this project's
Phase 7 scope (append-only audit trail is worth more than the ability to
delete inconvenient history); a future retention-purge job that
genuinely needs to remove old rows would need to explicitly
`DROP TRIGGER audit_logs_immutable` first, run the purge, and re-create
it -- a deliberate, visible, two-step operation rather than a silent
capability every connection has by default.
"""
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "52011ed7e2e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_log_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only: % is not permitted', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_immutable
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation()
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS audit_logs_immutable ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_mutation()")
