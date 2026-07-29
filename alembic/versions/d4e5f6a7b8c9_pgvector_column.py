"""add pgvector column for production-scale similarity search

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-29

Postgres-only, no-op on SQLite (same pattern as the RLS migrations).
Adds a native `vector(768)` column to knowledge_chunks alongside the
existing JSON `embedding` column, plus an HNSW index for fast
approximate cosine search. Raw SQL throughout -- deliberately no
dependency on the `pgvector` Python package here, so this migration file
imports cleanly even in a Termux/SQLite install that never runs it.

The JSON `embedding` column stays the canonical, portable source of
truth (works on SQLite, doesn't require the Postgres vector extension);
embedding_vector is written alongside it only when the app is running
against Postgres (see app/knowledge/router.py's ingest handler) and is
purely a performance optimization for top_k() at scale -- the pure-Python
cosine path over the JSON column remains correct and available as a
fallback on any database.

Dimension is fixed at 768 to match the default embedding model
(nomic-embed-text) and app.core.config.Settings.VECTOR_DIMENSIONS.
Switching to an embedding model with a different output size requires a
new migration to ALTER the column (pgvector columns are fixed-dimension)
-- a real, disclosed constraint, not silently handled.
"""
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None

VECTOR_DIMENSIONS = 768


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(f"ALTER TABLE knowledge_chunks ADD COLUMN embedding_vector vector({VECTOR_DIMENSIONS})")
    op.execute(
        "CREATE INDEX knowledge_chunks_embedding_hnsw_idx "
        "ON knowledge_chunks USING hnsw (embedding_vector vector_cosine_ops)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS knowledge_chunks_embedding_hnsw_idx")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS embedding_vector")
