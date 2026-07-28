"""
Phase 2: department registry + generic invoke endpoint, approval queue,
and the CEO briefing dashboard endpoint. The AI Gateway calls in this
sandbox have no real provider credentials/network (no ANTHROPIC_API_KEY,
no local Ollama) -- that's a real, expected condition (a fresh install
before the founder has added keys), and exactly what needs to degrade
gracefully rather than 500. These tests assert the graceful-failure path
explicitly, on top of the auth/RBAC/DB paths that don't depend on any
external service.
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


def _register(tenant_slug="acme", email="founder@acme.test", password="supersecret1"):
    return client.post(
        "/api/v1/auth/register",
        json={"tenant_name": "Acme Inc", "tenant_slug": tenant_slug, "email": email, "password": password},
    )


def _auth_headers(tenant_slug, token):
    return {"X-Tenant-ID": tenant_slug, "Authorization": f"Bearer {token}"}


# --- department registry ---

def test_list_departments_returns_all_26():
    reg = _register(tenant_slug="d1")
    token = reg.json()["access_token"]
    resp = client.get("/api/v1/departments", headers=_auth_headers("d1", token))
    assert resp.status_code == 200
    slugs = {d["slug"] for d in resp.json()["departments"]}
    assert len(slugs) == 26
    assert "executive_office" in slugs
    assert "engineering" in slugs
    assert "finance" in slugs


def test_invoke_unknown_department_returns_404():
    reg = _register(tenant_slug="d2")
    token = reg.json()["access_token"]
    resp = client.post(
        "/api/v1/departments/not_a_real_department/invoke",
        headers=_auth_headers("d2", token),
        json={"prompt": "hello"},
    )
    assert resp.status_code == 404


def test_invoke_requires_auth():
    resp = client.post(
        "/api/v1/departments/engineering/invoke",
        headers={"X-Tenant-ID": "acme"},
        json={"prompt": "hello"},
    )
    assert resp.status_code == 401


def test_invoke_requires_prompt():
    reg = _register(tenant_slug="d3")
    token = reg.json()["access_token"]
    resp = client.post(
        "/api/v1/departments/engineering/invoke",
        headers=_auth_headers("d3", token),
        json={},
    )
    assert resp.status_code == 400


def test_invoke_with_no_provider_configured_returns_503_not_500():
    """Owner has no permission barrier (bypass), so this exercises the real
    AI Gateway call path and its graceful failure -- not the RBAC gate."""
    reg = _register(tenant_slug="d4")
    token = reg.json()["access_token"]
    resp = client.post(
        "/api/v1/departments/engineering/invoke",
        headers=_auth_headers("d4", token),
        json={"prompt": "Say hello"},
    )
    assert resp.status_code == 503
    assert "detail" in resp.json()


def test_owner_bypasses_department_permission_gate_before_hitting_gateway():
    """Distinguishes a 401/403 (RBAC) from the 503 (gateway) failure mode:
    a non-owner without the permission must be denied at 403 and never
    reach the (failing) gateway call at all."""
    from app.auth.models import User
    from app.core.database import AsyncSessionLocal
    from app.core.security import create_access_token, hash_password

    async def _make_plain_user(tenant_id):
        async with AsyncSessionLocal() as session:
            user = User(
                tenant_id=tenant_id,
                email="staff@d5.test",
                hashed_password=hash_password("staffpassword1"),
                is_platform_admin=False,
                is_tenant_owner=False,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    reg = _register(tenant_slug="d5")
    tenant_id = reg.json()["tenant_id"]
    user = asyncio.run(_make_plain_user(tenant_id))
    token = create_access_token(subject=user.id, tenant_id=tenant_id)

    resp = client.post(
        "/api/v1/departments/engineering/invoke",
        headers=_auth_headers("d5", token),
        json={"prompt": "hello"},
    )
    assert resp.status_code == 403


# --- approval queue ---

def test_create_and_list_approval():
    reg = _register(tenant_slug="a1")
    token = reg.json()["access_token"]
    headers = _auth_headers("a1", token)

    create = client.post(
        "/api/v1/approvals", headers=headers,
        json={"title": "Approve $500 vendor payment", "department": "finance"},
    )
    assert create.status_code == 201
    assert create.json()["status"] == "pending"

    listing = client.get("/api/v1/approvals", headers=headers)
    assert listing.status_code == 200
    titles = [a["title"] for a in listing.json()["approvals"]]
    assert "Approve $500 vendor payment" in titles


def test_create_approval_requires_title():
    reg = _register(tenant_slug="a2")
    token = reg.json()["access_token"]
    resp = client.post("/api/v1/approvals", headers=_auth_headers("a2", token), json={})
    assert resp.status_code == 400


def test_owner_can_decide_approval():
    reg = _register(tenant_slug="a3")
    token = reg.json()["access_token"]
    headers = _auth_headers("a3", token)

    created = client.post("/api/v1/approvals", headers=headers, json={"title": "Sign contract"}).json()
    decide = client.post(
        f"/api/v1/approvals/{created['id']}/decide", headers=headers, json={"decision": "approved"}
    )
    assert decide.status_code == 200
    assert decide.json()["status"] == "approved"


def test_non_owner_cannot_decide_approval():
    from app.auth.models import User
    from app.core.database import AsyncSessionLocal
    from app.core.security import create_access_token, hash_password

    async def _make_plain_user(tenant_id):
        async with AsyncSessionLocal() as session:
            user = User(
                tenant_id=tenant_id, email="staff@a4.test",
                hashed_password=hash_password("staffpassword1"),
                is_platform_admin=False, is_tenant_owner=False,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    reg = _register(tenant_slug="a4")
    tenant_id = reg.json()["tenant_id"]
    owner_token = reg.json()["access_token"]
    created = client.post(
        "/api/v1/approvals", headers=_auth_headers("a4", owner_token), json={"title": "Sign contract"}
    ).json()

    staff = asyncio.run(_make_plain_user(tenant_id))
    staff_token = create_access_token(subject=staff.id, tenant_id=tenant_id)

    resp = client.post(
        f"/api/v1/approvals/{created['id']}/decide",
        headers=_auth_headers("a4", staff_token),
        json={"decision": "approved"},
    )
    assert resp.status_code == 403


def test_deciding_twice_is_rejected():
    reg = _register(tenant_slug="a5")
    token = reg.json()["access_token"]
    headers = _auth_headers("a5", token)
    created = client.post("/api/v1/approvals", headers=headers, json={"title": "Sign contract"}).json()
    client.post(f"/api/v1/approvals/{created['id']}/decide", headers=headers, json={"decision": "approved"})
    second = client.post(f"/api/v1/approvals/{created['id']}/decide", headers=headers, json={"decision": "rejected"})
    assert second.status_code == 409


def test_invalid_decision_value_rejected():
    reg = _register(tenant_slug="a6")
    token = reg.json()["access_token"]
    headers = _auth_headers("a6", token)
    created = client.post("/api/v1/approvals", headers=headers, json={"title": "Sign contract"}).json()
    resp = client.post(f"/api/v1/approvals/{created['id']}/decide", headers=headers, json={"decision": "maybe"})
    assert resp.status_code == 400


# --- dashboard / CEO briefing ---

def test_briefing_requires_owner():
    from app.auth.models import User
    from app.core.database import AsyncSessionLocal
    from app.core.security import create_access_token, hash_password

    async def _make_plain_user(tenant_id):
        async with AsyncSessionLocal() as session:
            user = User(
                tenant_id=tenant_id, email="staff@b1.test",
                hashed_password=hash_password("staffpassword1"),
                is_platform_admin=False, is_tenant_owner=False,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    reg = _register(tenant_slug="b1")
    tenant_id = reg.json()["tenant_id"]
    staff = asyncio.run(_make_plain_user(tenant_id))
    staff_token = create_access_token(subject=staff.id, tenant_id=tenant_id)

    resp = client.get("/api/v1/dashboard/briefing", headers=_auth_headers("b1", staff_token))
    assert resp.status_code == 403


def test_briefing_returns_real_counts_with_graceful_ai_fallback():
    reg = _register(tenant_slug="b2")
    token = reg.json()["access_token"]
    headers = _auth_headers("b2", token)

    client.post("/api/v1/approvals", headers=headers, json={"title": "Pending item one"})
    client.post("/api/v1/approvals", headers=headers, json={"title": "Pending item two"})

    resp = client.get("/api/v1/dashboard/briefing", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["pending_approvals"] == 2
    assert set(body["pending_approval_titles"]) == {"Pending item one", "Pending item two"}
    # No provider configured in this environment -- must degrade gracefully,
    # not fail the whole request.
    assert body["ai_synthesis"] is None
    assert body["ai_synthesis_error"] is not None


def test_audit_log_records_department_invoke_failure():
    from sqlalchemy import select
    from app.audit.logger import AuditLog
    from app.core.database import AsyncSessionLocal

    reg = _register(tenant_slug="b3")
    token = reg.json()["access_token"]
    client.post(
        "/api/v1/departments/marketing/invoke",
        headers=_auth_headers("b3", token),
        json={"prompt": "Draft a tweet"},
    )

    async def _check():
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.action == "marketing.invoke_failed")
            )
            return result.scalar_one_or_none()

    entry = asyncio.run(_check())
    assert entry is not None
