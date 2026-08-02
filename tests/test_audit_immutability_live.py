"""
Live integration test proving the audit_logs immutability trigger
actually blocks UPDATE and DELETE on real Postgres -- not just that the
trigger SQL was applied. Skipped by default unless
AUDIT_IMMUTABILITY_LIVE_TEST=true, same pattern as this project's other
live tests.

To run: apply migrations against a real Postgres database first
(as a privileged role -- CREATE TRIGGER needs it), then:
    AUDIT_IMMUTABILITY_LIVE_TEST=true DATABASE_URL=postgresql+asyncpg://... pytest tests/test_audit_immutability_live.py -v
Works whether DATABASE_URL points at a superuser or the non-superuser
saeos_app-equivalent role -- the trigger is role-independent by design
(see the migration's docstring for why that was chosen over a REVOKE).
"""
import os
import uuid

import pytest

RUN_LIVE = os.environ.get("AUDIT_IMMUTABILITY_LIVE_TEST") == "true"
pytestmark = pytest.mark.skipif(not RUN_LIVE, reason="AUDIT_IMMUTABILITY_LIVE_TEST not set -- skipping live test")


@pytest.mark.asyncio
async def test_audit_log_update_and_delete_are_blocked_on_real_postgres():
    from sqlalchemy import text
    from app.core.database import AsyncSessionLocal, engine, set_tenant_context

    assert engine.dialect.name == "postgresql", "This test requires a real Postgres DATABASE_URL"

    audit_id = f"test-immut-{uuid.uuid4().hex[:8]}"
    tenant_id = f"test-tenant-{uuid.uuid4().hex[:8]}"

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, tenant_id)
        await session.execute(
            text(
                "INSERT INTO audit_logs (id, tenant_id, actor_id, action, resource, metadata_json) "
                "VALUES (:id, :tenant_id, 'test-actor', 'test.action', '', '{}')"
            ),
            {"id": audit_id, "tenant_id": tenant_id},
        )
        await session.commit()

    with pytest.raises(Exception, match="append-only"):
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, tenant_id)
            await session.execute(text("UPDATE audit_logs SET action = 'tampered' WHERE id = :id"), {"id": audit_id})
            await session.commit()

    with pytest.raises(Exception, match="append-only"):
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, tenant_id)
            await session.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": audit_id})
            await session.commit()

    # The row must still exist, completely unmodified by either blocked attempt.
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, tenant_id)
        result = await session.execute(text("SELECT action FROM audit_logs WHERE id = :id"), {"id": audit_id})
        row = result.fetchone()
        assert row is not None, "row was deleted despite the trigger"
        assert row[0] == "test.action", "row was modified despite the trigger"
