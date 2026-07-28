"""
KnowledgeChunk -- the storage side of RAG. Embeddings are stored as a
JSON-encoded list of floats in a SQLite/Postgres TEXT/JSON column and
similarity is computed in pure Python (see vector_store.py). This is
deliberately not pgvector or a dedicated vector DB: those aren't
Termux-installable without native compilation (same class of problem as
pydantic-core), and pure-Python cosine similarity over a few thousand
chunks is genuinely fine for a single-tenant knowledge base at this
scale. See docs/TERMUX.md and vector_store.py's module docstring for the
production upgrade path.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Text, JSON, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)

    department: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(255), default="")  # e.g. filename, URL, "manual"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list] = mapped_column(JSON, nullable=False)  # list[float]
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
