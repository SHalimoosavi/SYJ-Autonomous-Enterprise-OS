"""
Live integration test proving app.core.database.set_tenant_context()
actually works against real Postgres -- not just that RLS policies exist
in the schema. Skipped by default unless RLS_LIVE_TEST=true, same
pattern as the other Phase 4/5 live tests.

This test exists because of a real bug found in Phase 6: set_tenant_context
used `SET LOCAL app.current_tenant_id = :tenant_id`, which is not valid
Postgres syntax -- SET does not accept bind parameters in that position,
and every earlier phase's "live Postgres" verification happened to never
call this function through a real HTTP request against Postgres (the
pgvector live test bypassed it, the Celery live test ran against
SQLite). So it was broken, undetected, since it was written. Fixed with
set_config(), a properly parameterized equivalent. This test exercises
the exact path that surfaced it: a full HTTP request causing
get_current_user() -> set_tenant_context() -> an RLS-protected read,
then a subsequent RLS-protected write (an audit log row) in the same
request.

Deliberately uses httpx.AsyncClient against an ASGITransport within a
single @pytest.mark.asyncio test, NOT Starlette's sync TestClient --
mixing sync TestClient's own event-loop/portal handling with a real
asyncpg connection pool produced "Future attached to a different loop"
RuntimeErrors while developing this test (a real, reproducible issue
with that combination, not application code), whereas driving the ASGI
app directly from one consistently-managed async test function works
correctly. Same pattern this project already uses successfully in
test_pgvector_live.py and test_async_workflow_live.py.

To run: apply migrations against a real Postgres+pgvector database
first, then:
    DATABASE_URL=postgresql+asyncpg://... alembic upgrade head
    RLS_LIVE_TEST=true DATABASE_URL=postgresql+asyncpg://... pytest tests/test_rls_live.py -v
"""
import os
import uuid

import pytest

RUN_LIVE = os.environ.get("RLS_LIVE_TEST") == "true"
pytestmark = pytest.mark.skipif(not RUN_LIVE, reason="RLS_LIVE_TEST not set -- skipping live Postgres RLS test")


@pytest.mark.asyncio
async def test_full_http_request_cycle_against_live_postgres_rls():
    import httpx
    from app.core.config import get_settings
    get_settings.cache_clear()

    from app.core.database import engine
    assert engine.dialect.name == "postgresql", "This test requires a real Postgres DATABASE_URL"

    from app.main import app

    tenant_slug = f"rls-live-{uuid.uuid4().hex[:8]}"  # unique per run: no cleanup step needed

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        reg = await client.post(
            "/api/v1/auth/register",
            json={"tenant_name": "RLS Live", "tenant_slug": tenant_slug,
                  "email": f"founder@{tenant_slug}.test", "password": "correct-horse-1"},
        )
        assert reg.status_code == 201, reg.text
        token = reg.json()["access_token"]
        headers = {"X-Tenant-ID": tenant_slug, "Authorization": f"Bearer {token}"}

        # Exercises get_current_user() -> set_tenant_context() -> RLS SELECT,
        # then (on failure, expected here with no AI provider) a
        # set_tenant_context() -> RLS INSERT for the audit log.
        resp = await client.post("/api/v1/departments/engineering/invoke", headers=headers, json={"prompt": "test"})
        assert resp.status_code == 503, f"expected graceful 503, got {resp.status_code}: {resp.text}"

        # RLS-protected INSERT + SELECT on approval_requests.
        approval = await client.post("/api/v1/approvals", headers=headers, json={"title": "RLS live test approval"})
        assert approval.status_code == 201, approval.text
        listing = await client.get("/api/v1/approvals", headers=headers)
        assert listing.status_code == 200
        assert any(a["title"] == "RLS live test approval" for a in listing.json()["approvals"])

        # RLS-protected INSERT (permission catalog auto-seed) + SELECT.
        perms = await client.get("/api/v1/permissions", headers=headers)
        assert perms.status_code == 200
        assert len(perms.json()["permissions"]) == 26
