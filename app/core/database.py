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

    Uses Postgres's set_config() function, NOT a bare `SET LOCAL x = :y`
    statement. Postgres's SET command does not accept bind parameters in
    that position at all -- `SET LOCAL app.current_tenant_id = :tenant_id`
    fails with a syntax error the moment it's actually prepared against a
    real server (asyncpg: "syntax error at or near $1"). set_config(name,
    value, is_local) is the standard, properly parameterized equivalent;
    is_local=true gives the same transaction-scoped reset behavior as
    SET LOCAL. This was a real, live bug: every earlier phase's "verified
    against live Postgres" claims for RLS either inspected schema/policies
    directly or exercised the vector-store/workflow code paths without
    ever going through get_current_user()'s call to this function on a
    real Postgres connection -- so it went undetected until Phase 6
    actually ran a full HTTP request against live Postgres end-to-end.

    No-op on SQLite (Termux/dev): SQLite has no RLS equivalent, and
    application-level filtering is the only enforcement mechanism there.
    """
    if engine.dialect.name == "postgresql":
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id}
        )
        # Explicitly clear the platform-admin bypass flag on every normal
        # tenant-scoped call, rather than assuming it always resets
        # cleanly on its own between separate AsyncSessionLocal() blocks.
        # A real, serious leak was caught live against Postgres during
        # Phase 6 development: is_local=true / SET LOCAL settings are
        # documented to reset at transaction end, but a pooled connection
        # handed to a new session without a fully clean transaction
        # boundary let a *previous* request's set_platform_admin_context()
        # bleed into a completely unrelated later request, making a normal
        # tenant-scoped query return another tenant's row. Not worth
        # tracking down the exact pooling mechanics further: explicitly
        # asserting "this is NOT a platform-admin session" every time a
        # tenant is scoped closes the leak regardless of the underlying
        # cause, and costs one cheap extra statement per request.
        await session.execute(text("SELECT set_config('app.is_platform_admin', 'false', true)"))


async def set_platform_admin_context(session: AsyncSession) -> None:
    """
    Enables the platform-admin RLS bypass (see
    alembic/versions/e5f6a7b8c9d0_rls_platform_admin_bypass.py) for the
    current transaction, so a cross-tenant query genuinely returns rows
    from every tenant instead of being silently filtered to zero by RLS.

    Callers MUST have already verified user.is_platform_admin via the
    normal auth path before calling this -- it is not itself an
    authorization check, just the mechanism that makes an
    already-authorized cross-tenant query actually work against
    Postgres. See app/platform/router.py for the only legitimate caller.

    No-op on SQLite, same as set_tenant_context: there's no RLS to bypass
    there, and application code in app/platform/router.py simply omits
    the tenant_id filter from its queries on that path already.
    """
    if engine.dialect.name == "postgresql":
        await session.execute(text("SELECT set_config('app.is_platform_admin', 'true', true)"))
