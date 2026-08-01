"""
Write path for the audit log. Every privileged action should call this:
AI Gateway invocations, permission denials, approval decisions, auth
events. Reads (list/query) belong in a dashboard/reporting module, not
here -- this file is intentionally write-only and side-effect-light so
it's safe to call from anywhere without creating import cycles.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import AuditLog
from app.core.database import set_tenant_context


async def record_audit(
    session: AsyncSession,
    tenant_id: str,
    actor_id: str,
    action: str,
    resource: str = "",
    metadata: dict | None = None,
) -> None:
    # Sets tenant context itself rather than trusting the caller's context
    # from earlier in the same session to still be valid. A real,
    # significant bug found live-testing against Postgres with RLS
    # actually enforced (Phase 6): Postgres's set_config(..., is_local=true)
    # -- what set_tenant_context uses -- resets at every COMMIT, and
    # several callers here called record_audit() *after* their own
    # `await session.commit()`, by which point the earlier
    # set_tenant_context() call no longer had any effect. The audit
    # INSERT's RLS WITH CHECK clause would then reject the row outright.
    # Making this function self-sufficient closes that gap for every
    # caller at once, rather than requiring each one to remember to
    # re-call set_tenant_context() immediately before every record_audit()
    # that happens to follow a commit().
    await set_tenant_context(session, tenant_id)
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            resource=resource,
            metadata_json=metadata or {},
        )
    )
    await session.commit()
