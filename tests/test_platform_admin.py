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
