"""
Shared approval-creation logic. Originally this was inline in
app/approvals/router.py's create_approval() handler only; Phase 6 needs
a second caller (app/api/v1/departments_router.py's automatic
escalation path) to create an ApprovalRequest the exact same way a
human-initiated one is created, so it's a real function now rather than
being duplicated.
"""
from app.approvals.models import ApprovalRequest


async def create_approval(session, tenant_id: str, department: str, title: str, description: str, requested_by: str) -> ApprovalRequest:
    approval = ApprovalRequest(
        tenant_id=tenant_id,
        department=department,
        title=title[:255],
        description=description,
        requested_by=requested_by,
    )
    session.add(approval)
    await session.commit()
    # No refresh(): id is a client-side UUID default; no caller uses
    # created_at from the returned object immediately after creation.
    return approval
