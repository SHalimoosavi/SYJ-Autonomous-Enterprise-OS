"""
Shared credential-verification logic, used by both the JSON API login
(app/auth/router.py) and the HTML admin UI login (app/admin/router.py).
One implementation, one set of tests -- the admin UI login was NOT worth
a second, parallel "verify this password" code path that could quietly
drift out of sync with (or be less carefully tested than) the API's.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.core.database import set_tenant_context
from app.core.security import verify_password
from app.tenancy.service import resolve_tenant_by_slug


async def authenticate(session: AsyncSession, tenant_slug: str | None, email: str, password: str) -> User | None:
    """Returns the User if tenant_slug/email/password are all correct,
    None otherwise. Deliberately returns the same None for "no such
    tenant", "no such user", and "wrong password" -- callers must not
    build different error messages for these cases (leaks which part was
    wrong)."""
    tenant = await resolve_tenant_by_slug(session, tenant_slug)
    if tenant is None:
        return None

    # Set RLS context using the resolved UUID, not the slug passed in --
    # a real bug caught during Phase 5 development before it shipped:
    # set_tenant_context expects tenants.id, and passing the slug through
    # would have been a silent no-op everywhere except live Postgres.
    await set_tenant_context(session, tenant.id)

    result = await session.execute(
        select(User).where(User.tenant_id == tenant.id, User.email == email.strip().lower())
    )
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.hashed_password):
        return None
    return user
