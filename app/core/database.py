"""
Async SQLAlchemy engine/session setup. Driver swaps via DATABASE_URL only —
no code changes needed to move from SQLite (Termux dev) to Postgres (prod).
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def set_tenant_context(session: AsyncSession, tenant_id: str) -> None:
    """
    Binds the active tenant_id for the current transaction so that Postgres
    row-level security policies (see alembic/versions/*_rls.py) can read it
    via current_setting('app.current_tenant_id'). This is on top of, not
    instead of, the application-level tenant filtering already enforced by
    the middleware and explicit query scoping -- defense in depth.

    No-op on SQLite (Termux/dev): SQLite has no RLS equivalent, and
    application-level filtering is the only enforcement mechanism there.
    """
    if engine.dialect.name == "postgresql":
        await session.execute(text("SET LOCAL app.current_tenant_id = :tenant_id"), {"tenant_id": tenant_id})
