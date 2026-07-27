"""
Tenant resolution helpers, shared by every auth/DB-touching handler.

Important naming note: request.state.tenant_id (set by
TenantContextMiddleware from the X-Tenant-ID header or subdomain) holds
the tenant's human-facing *slug* (e.g. "acme"), not its database UUID.
The middleware stays DB-free by design (cheap presence check on every
request, no query). Anything that actually touches the database -- login,
register, get_current_user -- must resolve that slug to the real
Tenant.id (UUID) via resolve_tenant_by_slug() before using it in a query
or embedding it in a JWT. This is the one place that resolution happens,
so it can't drift out of sync between call sites.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tenancy.models import Tenant


async def resolve_tenant_by_slug(session: AsyncSession, slug: str | None) -> Tenant | None:
    if not slug:
        return None
    result = await session.execute(select(Tenant).where(Tenant.slug == slug))
    return result.scalar_one_or_none()
