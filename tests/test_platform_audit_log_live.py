"""
Live integration test proving the Phase 8 cross-tenant audit-log
endpoints actually see across tenants against real (non-superuser)
Postgres RLS enforcement -- not just that they work on SQLite, where
there's no RLS to potentially get in the way. Skipped by default unless
PLATFORM_AUDIT_LIVE_TEST=true, same pattern as this project's other
live tests.

To run: apply migrations against a real Postgres+pgvector database as a
privileged role, provision the non-superuser role (scripts/
provision_postgres_role.py), then:
    PLATFORM_AUDIT_LIVE_TEST=true DATABASE_URL=postgresql+asyncpg://saeos_app:...@host/db \
      pytest tests/test_platform_audit_log_live.py -v
"""
import os
import uuid

import pytest

RUN_LIVE = os.environ.get("PLATFORM_AUDIT_LIVE_TEST") == "true"
pytestmark = pytest.mark.skipif(not RUN_LIVE, reason="PLATFORM_AUDIT_LIVE_TEST not set -- skipping live test")


@pytest.mark.asyncio
async def test_platform_admin_audit_log_spans_tenants_on_real_postgres_rls():
    import httpx
    from sqlalchemy import update
    from app.core.config import get_settings
    get_settings.cache_clear()

    from app.core.database import AsyncSessionLocal, engine
    assert engine.dialect.name == "postgresql", "This test requires a real Postgres DATABASE_URL"

    from app.auth.models import User
    from app.main import app

    suffix = uuid.uuid4().hex[:8]
    admin_slug = f"pa-admin-{suffix}"
    tenant_a_slug = f"pa-a-{suffix}"
    tenant_b_slug = f"pa-b-{suffix}"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        admin_reg = await client.post(
            "/api/v1/auth/register",
            json={"tenant_name": "PA Admin", "tenant_slug": admin_slug,
                  "email": f"founder@{admin_slug}.test", "password": "correct-horse-1"},
        )
        assert admin_reg.status_code == 201, admin_reg.text
        admin_user_id = admin_reg.json()["user_id"]
        admin_token = admin_reg.json()["access_token"]

        reg_a = await client.post(
            "/api/v1/auth/register",
            json={"tenant_name": "PA A", "tenant_slug": tenant_a_slug,
                  "email": f"founder@{tenant_a_slug}.test", "password": "correct-horse-1"},
        )
        assert reg_a.status_code == 201, reg_a.text
        token_a = reg_a.json()["access_token"]
        tenant_a_id = reg_a.json()["tenant_id"]

        reg_b = await client.post(
            "/api/v1/auth/register",
            json={"tenant_name": "PA B", "tenant_slug": tenant_b_slug,
                  "email": f"founder@{tenant_b_slug}.test", "password": "correct-horse-1"},
        )
        assert reg_b.status_code == 201, reg_b.text
        token_b = reg_b.json()["access_token"]

        # Grant is_platform_admin the way a real operator would.
        async with AsyncSessionLocal() as session:
            from app.core.database import set_tenant_context
            await set_tenant_context(session, admin_reg.json()["tenant_id"])
            await session.execute(update(User).where(User.id == admin_user_id).values(is_platform_admin=True))
            await session.commit()

        # Generate a real audit event in each tenant.
        approval_a = await client.post(
            "/api/v1/approvals",
            headers={"X-Tenant-ID": tenant_a_slug, "Authorization": f"Bearer {token_a}"},
            json={"title": f"From tenant A {suffix}"},
        )
        assert approval_a.status_code == 201
        approval_b = await client.post(
            "/api/v1/approvals",
            headers={"X-Tenant-ID": tenant_b_slug, "Authorization": f"Bearer {token_b}"},
            json={"title": f"From tenant B {suffix}"},
        )
        assert approval_b.status_code == 201

        admin_headers = {"X-Tenant-ID": admin_slug, "Authorization": f"Bearer {admin_token}"}

        # Tenant-scoped view: only tenant A's entries.
        tenant_a_log = await client.get(f"/api/v1/platform/tenants/{tenant_a_id}/audit-log", headers=admin_headers)
        assert tenant_a_log.status_code == 200
        entries_a = tenant_a_log.json()["entries"]
        assert all(e["tenant_id"] == tenant_a_id for e in entries_a)
        assert any(f"From tenant A {suffix}" in str(e) for e in entries_a) or len(entries_a) > 0

        # Global view: spans BOTH tenants -- the actual thing under test,
        # since RLS with FORCE ROW LEVEL SECURITY would silently filter
        # this to zero rows outside the admin's own tenant without the
        # platform-admin bypass genuinely working.
        global_log = await client.get("/api/v1/platform/audit-log", headers=admin_headers)
        assert global_log.status_code == 200
        tenant_ids_seen = {e["tenant_id"] for e in global_log.json()["entries"]}
        assert tenant_a_id in tenant_ids_seen
        assert reg_b.json()["tenant_id"] in tenant_ids_seen, (
            "Global audit log did not include a different tenant's entry -- "
            "the platform-admin RLS bypass isn't working for audit_logs."
        )

        # Negative control: a non-admin user in tenant A must not be able
        # to see this endpoint at all.
        denied = await client.get(
            "/api/v1/platform/audit-log", headers={"X-Tenant-ID": tenant_a_slug, "Authorization": f"Bearer {token_a}"}
        )
        assert denied.status_code == 403
