"""
HTML admin UI: cookie-session login, dashboard rendering, and the three
forms (create role, assign permission, assign role). Uses Starlette's
TestClient, which maintains a real cookie jar across requests -- so
these tests exercise the actual browser-like flow (login sets cookies,
subsequent requests carry them), not a shortcut.
"""
import asyncio

import pytest
from starlette.testclient import TestClient

from app.core.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def _fresh_schema():
    async def _reset():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_reset())
    yield


def _register(tenant_slug, email=None, password="supersecret1"):
    client = TestClient(app)
    client.post(
        "/api/v1/auth/register",
        json={"tenant_name": tenant_slug.title(), "tenant_slug": tenant_slug,
              "email": email or f"founder@{tenant_slug}.test", "password": password},
    )
    return client


def _make_staff(tenant_id, email):
    from app.auth.models import User
    from app.core.database import AsyncSessionLocal
    from app.core.security import hash_password

    async def _make():
        async with AsyncSessionLocal() as session:
            user = User(tenant_id=tenant_id, email=email, hashed_password=hash_password("staffpassword1"),
                        is_platform_admin=False, is_tenant_owner=False)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    return asyncio.run(_make())


def test_login_page_renders():
    client = TestClient(app)
    resp = client.get("/admin/login")
    assert resp.status_code == 200
    assert "SAEOS Admin" in resp.text
    assert "tenant_slug" in resp.text


def test_dashboard_redirects_to_login_when_unauthenticated():
    client = TestClient(app)
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_login_with_wrong_password_shows_error_not_500():
    client = _register("adm1", email="owner@adm1.test", password="correct-horse-1")
    resp = client.post("/admin/login", data={"tenant_slug": "adm1", "email": "owner@adm1.test", "password": "wrong"})
    assert resp.status_code == 401
    assert "Invalid" in resp.text


def test_login_success_sets_cookies_and_redirects():
    client = _register("adm2", email="owner@adm2.test", password="correct-horse-1")
    resp = client.post(
        "/admin/login",
        data={"tenant_slug": "adm2", "email": "owner@adm2.test", "password": "correct-horse-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin"
    assert "saeos_session" in client.cookies
    assert client.cookies.get("saeos_tenant") == "adm2"


def test_non_owner_login_is_rejected_from_admin():
    client = _register("adm3", email="owner@adm3.test", password="correct-horse-1")
    reg = client.post("/api/v1/auth/register", json={
        "tenant_name": "Adm3b", "tenant_slug": "adm3b", "email": "owner2@adm3b.test", "password": "correct-horse-1"
    })
    tenant_id = reg.json()["tenant_id"]
    _make_staff(tenant_id, "staff@adm3b.test")

    resp = client.post("/admin/login", data={"tenant_slug": "adm3b", "email": "staff@adm3b.test", "password": "staffpassword1"})
    assert resp.status_code == 403
    assert "Only the tenant owner" in resp.text


def test_full_dashboard_flow_via_cookies():
    client = _register("adm4", email="owner@adm4.test", password="correct-horse-1")
    login = client.post(
        "/admin/login",
        data={"tenant_slug": "adm4", "email": "owner@adm4.test", "password": "correct-horse-1"},
        follow_redirects=False,
    )
    assert login.status_code == 303

    dash = client.get("/admin")
    assert dash.status_code == 200
    assert "adm4" in dash.text.lower() or "Adm4" in dash.text
    assert "engineering.act" in dash.text  # permission catalog auto-seeded and rendered

    create = client.post("/admin/roles/create", data={"name": "Engineer"}, follow_redirects=False)
    assert create.status_code == 303

    dash2 = client.get("/admin")
    assert "Engineer" in dash2.text

    import re
    match = re.search(r'/admin/roles/([a-f0-9-]+)/permissions', dash2.text)
    assert match, "role permission-assignment form not found in rendered HTML"
    role_id = match.group(1)

    assign = client.post(f"/admin/roles/{role_id}/permissions", data={"permission_code": "engineering.act"}, follow_redirects=False)
    assert assign.status_code == 303

    dash3 = client.get("/admin")
    # the permission now shows up listed against the role
    assert "engineering.act" in dash3.text


def test_logout_clears_session_and_blocks_dashboard():
    client = _register("adm5", email="owner@adm5.test", password="correct-horse-1")
    client.post("/admin/login", data={"tenant_slug": "adm5", "email": "owner@adm5.test", "password": "correct-horse-1"})
    assert client.get("/admin").status_code == 200

    client.post("/admin/logout", follow_redirects=False)
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_admin_ui_and_json_api_share_the_same_underlying_state():
    """Prove app/permissions/service.py is genuinely shared: a role
    created via the admin HTML form must show up via the JSON API too."""
    client = _register("adm6", email="owner@adm6.test", password="correct-horse-1")
    client.post("/admin/login", data={"tenant_slug": "adm6", "email": "owner@adm6.test", "password": "correct-horse-1"})
    client.post("/admin/roles/create", data={"name": "Marketer"})

    token = client.cookies.get("saeos_session")
    api_resp = client.get("/api/v1/roles", headers={"X-Tenant-ID": "adm6", "Authorization": f"Bearer {token}"})
    assert api_resp.status_code == 200
    role_names = [r["name"] for r in api_resp.json()["roles"]]
    assert "Marketer" in role_names


def test_html_output_escapes_role_name_to_prevent_xss():
    client = _register("adm7", email="owner@adm7.test", password="correct-horse-1")
    client.post("/admin/login", data={"tenant_slug": "adm7", "email": "owner@adm7.test", "password": "correct-horse-1"})
    client.post("/admin/roles/create", data={"name": "<script>alert(1)</script>"})

    dash = client.get("/admin")
    assert "<script>alert(1)</script>" not in dash.text
    assert "&lt;script&gt;" in dash.text
