"""
Write path for the audit log. Every privileged action should call this:
AI Gateway invocations, permission denials, approval decisions, auth
events. Reads (list/query) belong in a dashboard/reporting module, not
here -- this file is intentionally write-only and side-effect-light so
it's safe to call from anywhere without creating import cycles.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import AuditLog


async def record_audit(
    session: AsyncSession,
    tenant_id: str,
    actor_id: str,
    action: str,
    resource: str = "",
    metadata: dict | None = None,
) -> None:
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
