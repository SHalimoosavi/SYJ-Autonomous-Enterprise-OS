"""
Platform-admin cross-tenant view. There is deliberately no API endpoint
that grants is_platform_admin -- it's an operator-level action (you,
running a one-off DB update on yourself), not something reachable
through the tenant-scoped HTTP surface. Tests reflect that by setting
the flag directly, the same way a real operator would.
"""
import asyncio

import pytest
from starlette.testclient import TestClient

from app.core.database import Base, engine
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_schema():
    async def _reset():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_reset())
    yield


def _register(tenant_slug):
    return client.post(
        "/api/v1/auth/register",
        json={"tenant_name": tenant_slug.title(), "tenant_slug": tenant_slug,
              "email": f"founder@{tenant_slug}.test", "password": "supersecret1"},
    )


def _headers(tenant_slug, token):
    return {"X-Tenant-ID": tenant_slug, "Authorization": f"Bearer {token}"}


def _make_platform_admin(user_id):
    """Grants is_platform_admin the way a real operator would -- a direct
    DB update, not an API call (see module docstring)."""
    from sqlalchemy import update
    from app.auth.models import User
    from app.core.database import AsyncSessionLocal

    async def _grant():
        async with AsyncSessionLocal() as session:
            await session.execute(update(User).where(User.id == user_id).values(is_platform_admin=True))
            await session.commit()

    asyncio.run(_grant())


def test_regular_tenant_owner_cannot_access_platform_endpoints():
    reg = _register("pt1")
    token = reg.json()["access_token"]
    resp = client.get("/api/v1/platform/tenants", headers=_headers("pt1", token))
    assert resp.status_code == 403


def test_platform_admin_sees_tenants_across_the_whole_system():
    admin_reg = _register("pt-admin")
    _make_platform_admin(admin_reg.json()["user_id"])
    admin_token = admin_reg.json()["access_token"]

    _register("pt2")
    _register("pt3")

    resp = client.get("/api/v1/platform/tenants", headers=_headers("pt-admin", admin_token))
    assert resp.status_code == 200
    slugs = {t["slug"] for t in resp.json()["tenants"]}
    # sees its own tenant AND the two others created by different users --
    # the actual point of "cross-tenant"
    assert {"pt-admin", "pt2", "pt3"}.issubset(slugs)


def test_platform_admin_tenant_detail_shows_aggregate_stats():
    admin_reg = _register("pt-admin2")
    _make_platform_admin(admin_reg.json()["user_id"])
    admin_token = admin_reg.json()["access_token"]

    other_reg = _register("pt4")
    other_token = other_reg.json()["access_token"]
    other_tenant_id = other_reg.json()["tenant_id"]
    client.post("/api/v1/approvals", headers=_headers("pt4", other_token), json={"title": "Some approval"})

    resp = client.get(f"/api/v1/platform/tenants/{other_tenant_id}", headers=_headers("pt-admin2", admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == "pt4"
    assert body["user_count"] == 1
    assert body["pending_approvals"] == 1


def test_platform_admin_tenant_detail_404_for_unknown_tenant():
    admin_reg = _register("pt-admin3")
    _make_platform_admin(admin_reg.json()["user_id"])
    admin_token = admin_reg.json()["access_token"]

    resp = client.get(
        "/api/v1/platform/tenants/00000000-0000-0000-0000-000000000000",
        headers=_headers("pt-admin3", admin_token),
    )
    assert resp.status_code == 404


def test_platform_stats_aggregate_across_all_tenants():
    admin_reg = _register("pt-admin4")
    _make_platform_admin(admin_reg.json()["user_id"])
    admin_token = admin_reg.json()["access_token"]

    _register("pt5")
    _register("pt6")

    resp = client.get("/api/v1/platform/stats", headers=_headers("pt-admin4", admin_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_tenants"] >= 3  # pt-admin4, pt5, pt6 at minimum
    assert body["total_users"] >= 3


def test_non_admin_cannot_see_platform_stats():
    reg = _register("pt7")
    token = reg.json()["access_token"]
    resp = client.get("/api/v1/platform/stats", headers=_headers("pt7", token))
    assert resp.status_code == 403


def test_platform_admin_endpoints_require_auth():
    resp = client.get("/api/v1/platform/tenants", headers={"X-Tenant-ID": "pt1"})
    assert resp.status_code == 401


# --- Phase 8: cross-tenant audit-log view ---

def test_non_admin_cannot_see_tenant_audit_log():
    reg = _register("pt8")
    tenant_id = reg.json()["tenant_id"]
    token = reg.json()["access_token"]
    resp = client.get(f"/api/v1/platform/tenants/{tenant_id}/audit-log", headers=_headers("pt8", token))
    assert resp.status_code == 403


def test_tenant_audit_log_returns_only_that_tenants_entries():
    admin_reg = _register("pt-admin5")
    _make_platform_admin(admin_reg.json()["user_id"])
    admin_token = admin_reg.json()["access_token"]

    reg_a = _register("pt9")
    token_a = reg_a.json()["access_token"]
    tenant_a_id = reg_a.json()["tenant_id"]
    client.post("/api/v1/approvals", headers=_headers("pt9", token_a), json={"title": "Tenant A approval"})

    reg_b = _register("pt10")
    token_b = reg_b.json()["access_token"]
    client.post("/api/v1/approvals", headers=_headers("pt10", token_b), json={"title": "Tenant B approval"})

    resp = client.get(f"/api/v1/platform/tenants/{tenant_a_id}/audit-log", headers=_headers("pt-admin5", admin_token))
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert len(entries) > 0
    assert all(e["tenant_id"] == tenant_a_id for e in entries)
    assert any(e["action"] == "approval.created" for e in entries)


def test_tenant_audit_log_404_for_unknown_tenant():
    admin_reg = _register("pt-admin6")
    _make_platform_admin(admin_reg.json()["user_id"])
    admin_token = admin_reg.json()["access_token"]

    resp = client.get(
        "/api/v1/platform/tenants/00000000-0000-0000-0000-000000000000/audit-log",
        headers=_headers("pt-admin6", admin_token),
    )
    assert resp.status_code == 404


def test_tenant_audit_log_filters_by_action():
    admin_reg = _register("pt-admin7")
    _make_platform_admin(admin_reg.json()["user_id"])
    admin_token = admin_reg.json()["access_token"]

    reg = _register("pt11")
    token = reg.json()["access_token"]
    tenant_id = reg.json()["tenant_id"]
    client.post("/api/v1/approvals", headers=_headers("pt11", token), json={"title": "x"})

    resp = client.get(
        f"/api/v1/platform/tenants/{tenant_id}/audit-log?action=approval.created",
        headers=_headers("pt-admin7", admin_token),
    )
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert len(entries) > 0
    assert all(e["action"] == "approval.created" for e in entries)

    resp_none = client.get(
        f"/api/v1/platform/tenants/{tenant_id}/audit-log?action=nonexistent.action",
        headers=_headers("pt-admin7", admin_token),
    )
    assert resp_none.json()["entries"] == []


def test_global_audit_log_spans_multiple_tenants():
    admin_reg = _register("pt-admin8")
    _make_platform_admin(admin_reg.json()["user_id"])
    admin_token = admin_reg.json()["access_token"]

    reg_a = _register("pt12")
    token_a = reg_a.json()["access_token"]
    client.post("/api/v1/approvals", headers=_headers("pt12", token_a), json={"title": "From tenant pt12"})

    reg_b = _register("pt13")
    token_b = reg_b.json()["access_token"]
    client.post("/api/v1/approvals", headers=_headers("pt13", token_b), json={"title": "From tenant pt13"})

    resp = client.get("/api/v1/platform/audit-log", headers=_headers("pt-admin8", admin_token))
    assert resp.status_code == 200
    tenant_ids_seen = {e["tenant_id"] for e in resp.json()["entries"]}
    assert reg_a.json()["tenant_id"] in tenant_ids_seen
    assert reg_b.json()["tenant_id"] in tenant_ids_seen


def test_non_admin_cannot_see_global_audit_log():
    reg = _register("pt14")
    token = reg.json()["access_token"]
    resp = client.get("/api/v1/platform/audit-log", headers=_headers("pt14", token))
    assert resp.status_code == 403


def test_audit_log_limit_is_validated():
    admin_reg = _register("pt-admin9")
    _make_platform_admin(admin_reg.json()["user_id"])
    admin_token = admin_reg.json()["access_token"]
    headers = _headers("pt-admin9", admin_token)

    too_big = client.get("/api/v1/platform/audit-log?limit=99999", headers=headers)
    assert too_big.status_code == 400

    not_a_number = client.get("/api/v1/platform/audit-log?limit=abc", headers=headers)
    assert not_a_number.status_code == 400

    zero = client.get("/api/v1/platform/audit-log?limit=0", headers=headers)
    assert zero.status_code == 400

    valid = client.get("/api/v1/platform/audit-log?limit=5", headers=headers)
    assert valid.status_code == 200
