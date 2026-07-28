"""
End-to-end auth flow against the real (SQLite) DB: register -> login ->
access a protected route. Exercises the actual path that has to be
correct for RBAC and multi-tenant isolation to hold -- not mocked.
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


def test_register_creates_tenant_and_owner_user():
    resp = _register()
    assert resp.status_code == 201
    body = resp.json()
    assert body["tenant_slug"] == "acme"
    assert "access_token" in body


def test_register_does_not_require_tenant_header():
    resp = client.post(
        "/api/v1/auth/register",
        json={"tenant_name": "NoHeader Co", "tenant_slug": "noheader", "email": "a@noheader.test", "password": "supersecret1"},
    )
    assert resp.status_code == 201


def test_register_rejects_duplicate_tenant_slug():
    _register()
    resp = _register(email="someoneelse@acme.test")
    assert resp.status_code == 409


def test_register_rejects_short_password():
    resp = client.post(
        "/api/v1/auth/register",
        json={"tenant_name": "Acme", "tenant_slug": "acme2", "email": "a@a.test", "password": "short"},
    )
    assert resp.status_code == 400


def test_register_rejects_missing_fields():
    resp = client.post("/api/v1/auth/register", json={"tenant_name": "Acme"})
    assert resp.status_code == 400


def test_login_succeeds_with_correct_credentials():
    _register(tenant_slug="beta", email="owner@beta.test", password="correct-horse-1")
    resp = client.post(
        "/api/v1/auth/login",
        headers={"X-Tenant-ID": "beta"},
        json={"email": "owner@beta.test", "password": "correct-horse-1"},
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_rejects_wrong_password():
    _register(tenant_slug="gamma", email="owner@gamma.test", password="correct-horse-1")
    resp = client.post(
        "/api/v1/auth/login",
        headers={"X-Tenant-ID": "gamma"},
        json={"email": "owner@gamma.test", "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_login_rejects_unknown_email():
    resp = client.post(
        "/api/v1/auth/login",
        headers={"X-Tenant-ID": "acme"},
        json={"email": "nobody@acme.test", "password": "whatever12"},
    )
    assert resp.status_code == 401


def test_login_rejects_correct_credentials_under_wrong_tenant():
    """A valid user in tenant A must not authenticate against tenant B's context."""
    _register(tenant_slug="delta", email="owner@delta.test", password="correct-horse-1")
    resp = client.post(
        "/api/v1/auth/login",
        headers={"X-Tenant-ID": "some-other-tenant"},
        json={"email": "owner@delta.test", "password": "correct-horse-1"},
    )
    assert resp.status_code == 401


def test_me_requires_bearer_token():
    resp = client.get("/api/v1/auth/me", headers={"X-Tenant-ID": "acme"})
    assert resp.status_code == 401


def test_me_rejects_garbage_token():
    resp = client.get(
        "/api/v1/auth/me",
        headers={"X-Tenant-ID": "acme", "Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


def test_me_returns_current_user_with_valid_token():
    reg = _register(tenant_slug="epsilon", email="owner@epsilon.test", password="correct-horse-1")
    token = reg.json()["access_token"]
    resp = client.get(
        "/api/v1/auth/me",
        headers={"X-Tenant-ID": "epsilon", "Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "owner@epsilon.test"
    assert body["is_tenant_owner"] is True


def test_token_rejected_when_replayed_against_a_different_tenant_header():
    """The token is valid, but presenting it under another tenant's header
    must not resolve to a user -- tenant_id in the token and in the
    request context must match."""
    reg = _register(tenant_slug="zeta", email="owner@zeta.test", password="correct-horse-1")
    _register(tenant_slug="eta", email="owner@eta.test", password="correct-horse-1")
    token = reg.json()["access_token"]
    resp = client.get(
        "/api/v1/auth/me",
        headers={"X-Tenant-ID": "eta", "Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401

# Note: the old /api/v1/executive/briefing placeholder route from Phase 1.1
# was replaced in Phase 2 by the generic /api/v1/departments/{slug}/invoke
# endpoint. The owner-bypass and non-owner-denied RBAC behavior it used to
# test here is now covered more precisely in tests/test_departments_and_approvals.py
# (test_owner_bypasses_department_permission_gate_before_hitting_gateway and
# test_invoke_with_no_provider_configured_returns_503_not_500), which also
# distinguish the RBAC-denial path from the AI-Gateway-failure path.
