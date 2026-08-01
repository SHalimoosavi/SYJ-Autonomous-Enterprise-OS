"""
Live integration test proving the platform-admin RLS bypass actually
works against real Postgres -- not just that the policy SQL looks
right. Skipped by default unless PLATFORM_ADMIN_LIVE_TEST=true, same
pattern as this project's other live tests.

The whole point of this feature is that FORCE ROW LEVEL SECURITY (used
on every tenant-scoped table since Phase 1.1) makes even a superuser's
query subject to the policy -- so this specifically needs to be
verified against a real Postgres connection, where RLS is actually
enforced, not SQLite (no RLS at all, so a bug here would be invisible).

To run: apply migrations against a real Postgres+pgvector database
first, then:
    DATABASE_URL=postgresql+asyncpg://... alembic upgrade head
    PLATFORM_ADMIN_LIVE_TEST=true DATABASE_URL=postgresql+asyncpg://... pytest tests/test_platform_admin_live.py -v
"""
import os
import uuid

import pytest

RUN_LIVE = os.environ.get("PLATFORM_ADMIN_LIVE_TEST") == "true"
pytestmark = pytest.mark.skipif(not RUN_LIVE, reason="PLATFORM_ADMIN_LIVE_TEST not set -- skipping live test")


@pytest.mark.asyncio
async def test_platform_admin_sees_across_tenants_on_real_postgres_rls():
    import httpx
    from sqlalchemy import update
    from app.core.config import get_settings
    get_settings.cache_clear()

    from app.core.database import AsyncSessionLocal, engine, set_tenant_context
    assert engine.dialect.name == "postgresql", "This test requires a real Postgres DATABASE_URL"

    from app.auth.models import User
    from app.main import app

    suffix = uuid.uuid4().hex[:8]
    admin_slug = f"pg-admin-{suffix}"
    other_slug = f"pg-other-{suffix}"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        admin_reg = await client.post(
            "/api/v1/auth/register",
            json={"tenant_name": "PG Admin", "tenant_slug": admin_slug,
                  "email": f"founder@{admin_slug}.test", "password": "correct-horse-1"},
        )
        assert admin_reg.status_code == 201, admin_reg.text
        admin_user_id = admin_reg.json()["user_id"]
        admin_token = admin_reg.json()["access_token"]

        other_reg = await client.post(
            "/api/v1/auth/register",
            json={"tenant_name": "PG Other", "tenant_slug": other_slug,
                  "email": f"founder@{other_slug}.test", "password": "correct-horse-1"},
        )
        assert other_reg.status_code == 201, other_reg.text

        # Grant is_platform_admin the way a real operator would.
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, admin_reg.json()["tenant_id"])
            await session.execute(update(User).where(User.id == admin_user_id).values(is_platform_admin=True))
            await session.commit()

        headers = {"X-Tenant-ID": admin_slug, "Authorization": f"Bearer {admin_token}"}

        # Sanity check: WITHOUT the bypass, RLS + FORCE ROW LEVEL SECURITY
        # would mean this only ever returns the admin's own tenant, never
        # "pg-other-...". This is the actual thing under test.
        resp = await client.get("/api/v1/platform/tenants", headers=headers)
        assert resp.status_code == 200, resp.text
        slugs = {t["slug"] for t in resp.json()["tenants"]}
        assert admin_slug in slugs
        assert other_slug in slugs, (
            "Platform admin did not see a different tenant's row -- the RLS "
            "bypass (set_platform_admin_context / the updated policy) isn't "
            "actually working against live Postgres."
        )

        # Negative control -- the actually important check: a REGULAR
        # (non-admin) user must still see ONLY their own tenant's data.
        # If this failed, it would mean the policy change accidentally
        # broke tenant isolation for everyone, not just added a scoped
        # admin bypass -- silently defeating the entire point of RLS.
        other_token = other_reg.json()["access_token"]
        other_headers = {"X-Tenant-ID": other_slug, "Authorization": f"Bearer {other_token}"}
        denied = await client.get("/api/v1/platform/tenants", headers=other_headers)
        assert denied.status_code == 403  # not a platform admin -- app-level check catches it first

        # And even bypassing the app-level check hypothetically, confirm
        # via a direct approval-queue call (tenant-scoped, not platform-only)
        # that this user's own queries still only ever see their own tenant.
        approvals = await client.get("/api/v1/approvals", headers=other_headers)
        assert approvals.status_code == 200
        assert approvals.json()["approvals"] == []  # this tenant created no approvals

        # The most rigorous check: bypass application-level checks
        # entirely and query at the DB layer directly with
        # set_tenant_context() (NOT set_platform_admin_context()) for the
        # admin's own tenant. If RLS itself were broken by the policy
        # change, this raw query -- with no app-level 403 in the way --
        # would still leak the other tenant's row. Uses ApprovalRequest,
        # not Tenant: the tenants table has no tenant_id column (it IS
        # the tenant) and was never RLS-protected in the first place, so
        # querying it here would prove nothing either way.
        create_other_approval = await client.post(
            "/api/v1/approvals", headers=other_headers, json={"title": "Should stay invisible to admin's raw session"}
        )
        assert create_other_approval.status_code == 201

        from sqlalchemy import select
        from app.approvals.models import ApprovalRequest
        from app.core.database import set_tenant_context

        admin_tenant_id = admin_reg.json()["tenant_id"]
        async with AsyncSessionLocal() as session:
            await set_tenant_context(session, admin_tenant_id)
            visible = (await session.execute(select(ApprovalRequest))).scalars().all()
        visible_titles = {a.title for a in visible}
        assert "Should stay invisible to admin's raw session" not in visible_titles, (
            "RLS leaked another tenant's approval_requests row to a normal (non-bypass) session"
        )
