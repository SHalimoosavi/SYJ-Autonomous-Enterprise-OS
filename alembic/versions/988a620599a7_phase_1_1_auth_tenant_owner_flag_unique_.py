"""phase 1.1 auth: tenant owner flag, unique tenant email

Revision ID: 988a620599a7
Revises: 6163ed7bf47d
Create Date: 2026-07-27 23:04:03.612735

Uses batch mode (op.batch_alter_table) rather than plain op.add_column /
op.create_unique_constraint: SQLite has no native ALTER TABLE ADD
CONSTRAINT, so autogenerate's default output fails on Termux/dev (SQLite)
even though it's valid on Postgres. Batch mode uses SQLAlchemy's
copy-and-move strategy on SQLite and a plain ALTER on Postgres, so this
migration runs identically on both. See env.py's render_as_batch=True,
which makes this the standard from here on.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '988a620599a7'
down_revision = '6163ed7bf47d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('is_tenant_owner', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.create_unique_constraint('uq_users_tenant_email', ['tenant_id', 'email'])


def downgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_constraint('uq_users_tenant_email', type_='unique')
        batch_op.drop_column('is_tenant_owner')
