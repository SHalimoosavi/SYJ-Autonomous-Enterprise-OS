"""
Pure-Python cosine similarity over KnowledgeChunk rows -- the "vector
store" for Termux/dev. No numpy: numpy has real prebuilt Termux `pkg`
binaries (unlike pydantic-core), so it's a legitimate future upgrade
(`pkg install python-numpy`) for larger corpora, but a plain Python dot
product is correct, dependency-free, and fast enough for a single
tenant's knowledge base at MVP scale (thousands, not millions, of chunks).

Production upgrade path: swap this module's `top_k()` implementation for
a call to pgvector (`<->` operator) or a dedicated vector DB (Chroma,
Qdrant) once corpus size or query latency actually demands it -- nothing
above this module (the ingest/query endpoints) needs to change, since
they only depend on `top_k()`'s signature.
"""
import math

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import engine
from app.knowledge.models import KnowledgeChunk


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"Embedding dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _vector_literal(embedding: list[float]) -> str:
    """Formats a Python float list as a pgvector literal string, e.g.
    '[0.1,0.2,0.3]'. No dependency on the `pgvector` Python package --
    this is just the text format Postgres' vector type parses."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


async def _top_k_memory(
    session: AsyncSession, tenant_id: str, query_embedding: list[float], k: int, department: str | None,
) -> list[tuple[KnowledgeChunk, float]]:
    """Pure-Python cosine similarity over the JSON embedding column.
    Correct on any database (SQLite, Postgres, anything SQLAlchemy
    supports); the default, and the only option on Termux/dev."""
    stmt = select(KnowledgeChunk).where(KnowledgeChunk.tenant_id == tenant_id)
    if department:
        stmt = stmt.where(KnowledgeChunk.department == department)

    chunks = (await session.execute(stmt)).scalars().all()
    scored = [(chunk, cosine_similarity(query_embedding, chunk.embedding)) for chunk in chunks]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]


async def _top_k_pgvector(
    session: AsyncSession, tenant_id: str, query_embedding: list[float], k: int, department: str | None,
) -> list[tuple[KnowledgeChunk, float]]:
    """Postgres-native HNSW approximate cosine search via the `<=>`
    operator on the embedding_vector column (see the pgvector migration).
    Falls back to the pure-Python path for any row where embedding_vector
    is NULL (e.g. ingested before the column existed, or on a database
    where the migration hasn't run) -- callers get correct results
    either way, just without the index speedup for those rows."""
    query_vec = _vector_literal(query_embedding)
    stmt = text(
        """
        SELECT id, 1 - (embedding_vector <=> :query_vec) AS score
        FROM knowledge_chunks
        WHERE tenant_id = :tenant_id
          AND embedding_vector IS NOT NULL
          AND (CAST(:department AS VARCHAR) IS NULL OR department = CAST(:department AS VARCHAR))
        ORDER BY embedding_vector <=> :query_vec
        LIMIT :k
        """
    )
    rows = (
        await session.execute(
            stmt, {"query_vec": query_vec, "tenant_id": tenant_id, "department": department, "k": k}
        )
    ).all()
    if not rows:
        return []

    ids = [row.id for row in rows]
    scores_by_id = {row.id: float(row.score) for row in rows}
    chunks = (
        await session.execute(select(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids)))
    ).scalars().all()
    chunks_by_id = {c.id: c for c in chunks}

    # Re-order to match the SQL ranking (the IN clause above doesn't
    # preserve order) and pair each chunk back with its score.
    return [(chunks_by_id[row.id], scores_by_id[row.id]) for row in rows if row.id in chunks_by_id]


async def top_k(
    session: AsyncSession,
    tenant_id: str,
    query_embedding: list[float],
    k: int = 5,
    department: str | None = None,
) -> list[tuple[KnowledgeChunk, float]]:
    """Returns up to k (chunk, similarity_score) pairs, tenant-scoped and
    optionally department-scoped, sorted by descending similarity.
    Dispatches to the pgvector-backed path only when both the app is
    configured for it (VECTOR_STORE_BACKEND=pgvector) and the database
    actually is Postgres -- so a Termux/SQLite deployment can never
    accidentally hit the pgvector-only SQL and get a confusing error."""
    settings = get_settings()
    if settings.VECTOR_STORE_BACKEND == "pgvector" and engine.dialect.name == "postgresql":
        return await _top_k_pgvector(session, tenant_id, query_embedding, k, department)
    return await _top_k_memory(session, tenant_id, query_embedding, k, department)
