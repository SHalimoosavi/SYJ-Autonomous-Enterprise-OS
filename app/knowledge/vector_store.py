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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def top_k(
    session: AsyncSession,
    tenant_id: str,
    query_embedding: list[float],
    k: int = 5,
    department: str | None = None,
) -> list[tuple[KnowledgeChunk, float]]:
    """Returns up to k (chunk, similarity_score) pairs, tenant-scoped and
    optionally department-scoped, sorted by descending similarity."""
    stmt = select(KnowledgeChunk).where(KnowledgeChunk.tenant_id == tenant_id)
    if department:
        stmt = stmt.where(KnowledgeChunk.department == department)

    chunks = (await session.execute(stmt)).scalars().all()
    scored = [(chunk, cosine_similarity(query_embedding, chunk.embedding)) for chunk in chunks]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]
