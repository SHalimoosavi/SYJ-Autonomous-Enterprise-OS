"""
Phase 3 endpoints: RAG (knowledge ingest/query), workflow engine, and
permission management. Same sandbox reality as Phase 2's tests: no real
embedding/generation provider is reachable here (no ANTHROPIC_API_KEY, no
local Ollama), so the AI-dependent paths (knowledge ingest/query,
workflow run) are tested for their graceful 503 failure -- a real,
expected first-install state -- while the DB-only paths (permission
management, workflow listing/404s) are tested against their actual
success behavior.
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


def _register(tenant_slug, email=None, password="supersecret1"):
    return client.post(
        "/api/v1/auth/register",
        json={
            "tenant_name": tenant_slug.title(),
            "tenant_slug": tenant_slug,
            "email": email or f"founder@{tenant_slug}.test",
            "password": password,
        },
    )


def _headers(tenant_slug, token):
    return {"X-Tenant-ID": tenant_slug, "Authorization": f"Bearer {token}"}


def _make_staff_user(tenant_id, tenant_slug, email):
    from app.auth.models import User
    from app.core.database import AsyncSessionLocal
    from app.core.security import create_access_token, hash_password

    async def _make():
        async with AsyncSessionLocal() as session:
            user = User(
                tenant_id=tenant_id, email=email,
                hashed_password=hash_password("staffpassword1"),
                is_platform_admin=False, is_tenant_owner=False,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    user = asyncio.run(_make())
    token = create_access_token(subject=user.id, tenant_id=tenant_id)
    return user, token


# --- knowledge base (RAG) ---

def test_knowledge_ingest_requires_content():
    reg = _register("k1")
    token = reg.json()["access_token"]
    resp = client.post("/api/v1/knowledge/ingest", headers=_headers("k1", token), json={})
    assert resp.status_code == 400


def test_knowledge_ingest_returns_503_with_no_embedding_provider():
    reg = _register("k2")
    token = reg.json()["access_token"]
    resp = client.post(
        "/api/v1/knowledge/ingest", headers=_headers("k2", token),
        json={"content": "Our refund policy is 30 days.", "department": "finance"},
    )
    assert resp.status_code == 503
    assert "detail" in resp.json()


def test_knowledge_query_returns_503_with_no_embedding_provider():
    reg = _register("k3")
    token = reg.json()["access_token"]
    resp = client.post(
        "/api/v1/knowledge/query", headers=_headers("k3", token),
        json={"query": "what is the refund policy?"},
    )
    assert resp.status_code == 503


def test_knowledge_query_requires_query_text():
    reg = _register("k4")
    token = reg.json()["access_token"]
    resp = client.post("/api/v1/knowledge/query", headers=_headers("k4", token), json={})
    assert resp.status_code == 400


def test_knowledge_query_with_manually_inserted_chunk_finds_it():
    """Bypasses the embed() call (no provider in this sandbox) by writing
    a KnowledgeChunk directly, to prove top_k()'s DB query + cosine
    ranking works correctly end-to-end -- only the embedding *generation*
    step is untestable here, not the retrieval logic itself."""
    from app.core.database import AsyncSessionLocal
    from app.knowledge.models import KnowledgeChunk
    from app.knowledge.vector_store import top_k

    reg = _register("k5")
    tenant_id = reg.json()["tenant_id"]

    async def _seed_and_query():
        async with AsyncSessionLocal() as session:
            session.add(KnowledgeChunk(
                tenant_id=tenant_id, department="finance", source="manual",
                content="Refunds are processed within 30 days.",
                embedding=[1.0, 0.0, 0.0], embedding_model="test",
            ))
            session.add(KnowledgeChunk(
                tenant_id=tenant_id, department="finance", source="manual",
                content="Our office is in Hyderabad.",
                embedding=[0.0, 1.0, 0.0], embedding_model="test",
            ))
            await session.commit()
            return await top_k(session, tenant_id, [0.9, 0.1, 0.0], k=1)

    results = asyncio.run(_seed_and_query())
    assert len(results) == 1
    assert "Refunds" in results[0][0].content


# --- workflow engine ---

def test_list_workflows():
    reg = _register("w1")
    token = reg.json()["access_token"]
    resp = client.get("/api/v1/workflows", headers=_headers("w1", token))
    assert resp.status_code == 200
    slugs = {w["slug"] for w in resp.json()["workflows"]}
    assert "release_review" in slugs
    assert "vendor_onboarding" in slugs


def test_run_unknown_workflow_returns_404():
    reg = _register("w2")
    token = reg.json()["access_token"]
    resp = client.post(
        "/api/v1/workflows/not_a_real_workflow/run", headers=_headers("w2", token), json={"input": "x"}
    )
    assert resp.status_code == 404


def test_run_workflow_requires_input():
    reg = _register("w3")
    token = reg.json()["access_token"]
    resp = client.post("/api/v1/workflows/release_review/run", headers=_headers("w3", token), json={})
    assert resp.status_code == 400


def test_run_workflow_fails_gracefully_with_no_provider_and_persists_partial_state():
    reg = _register("w4")
    token = reg.json()["access_token"]
    resp = client.post(
        "/api/v1/workflows/release_review/run", headers=_headers("w4", token),
        json={"input": "Add a new /health endpoint"},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "failed"
    assert body["failed_step"] == "engineering"  # first step in release_review
    assert body["completed_steps"] == []

    # The run itself must still be inspectable afterward, not lost.
    run_resp = client.get(f"/api/v1/workflows/runs/{body['run_id']}", headers=_headers("w4", token))
    assert run_resp.status_code == 200
    assert run_resp.json()["status"] == "failed"


def test_get_unknown_run_returns_404():
    reg = _register("w5")
    token = reg.json()["access_token"]
    resp = client.get("/api/v1/workflows/runs/00000000-0000-0000-0000-000000000000", headers=_headers("w5", token))
    assert resp.status_code == 404


def test_non_owner_without_department_permission_denied_before_run_starts():
    reg = _register("w6")
    tenant_id = reg.json()["tenant_id"]
    _, staff_token = _make_staff_user(tenant_id, "w6", "staff@w6.test")
    resp = client.post(
        "/api/v1/workflows/release_review/run", headers=_headers("w6", staff_token), json={"input": "x"}
    )
    assert resp.status_code == 403


# --- permission management ---

def test_list_permissions_seeds_catalog_from_registry():
    reg = _register("p1")
    token = reg.json()["access_token"]
    resp = client.get("/api/v1/permissions", headers=_headers("p1", token))
    assert resp.status_code == 200
    codes = {p["code"] for p in resp.json()["permissions"]}
    assert "engineering.act" in codes
    assert "finance.act" in codes
    assert "executive.view_briefing" in codes
    assert len(codes) == 26  # one per department


def test_non_owner_cannot_list_permissions():
    reg = _register("p2")
    tenant_id = reg.json()["tenant_id"]
    _, staff_token = _make_staff_user(tenant_id, "p2", "staff@p2.test")
    resp = client.get("/api/v1/permissions", headers=_headers("p2", staff_token))
    assert resp.status_code == 403


def test_create_role_and_assign_permission():
    reg = _register("p3")
    token = reg.json()["access_token"]
    headers = _headers("p3", token)

    role_resp = client.post("/api/v1/roles", headers=headers, json={"name": "Engineer"})
    assert role_resp.status_code == 201
    role_id = role_resp.json()["id"]

    assign_resp = client.post(
        f"/api/v1/roles/{role_id}/permissions", headers=headers, json={"permission_code": "engineering.act"}
    )
    assert assign_resp.status_code == 200

    roles_resp = client.get("/api/v1/roles", headers=headers)
    role = next(r for r in roles_resp.json()["roles"] if r["id"] == role_id)
    assert "engineering.act" in role["permissions"]


def test_assign_unknown_permission_code_returns_404():
    reg = _register("p4")
    token = reg.json()["access_token"]
    headers = _headers("p4", token)
    role_id = client.post("/api/v1/roles", headers=headers, json={"name": "Engineer"}).json()["id"]
    resp = client.post(f"/api/v1/roles/{role_id}/permissions", headers=headers, json={"permission_code": "not.a.real.code"})
    assert resp.status_code == 404


def test_assign_role_to_user_grants_department_access_without_full_ownership():
    """The actual point of this whole module: a staff user with a role
    that has engineering.act can invoke the engineering department even
    though they're not the tenant owner -- proven by getting a 503
    (reached the AI Gateway call) instead of a 403 (denied by RBAC)."""
    reg = _register("p5")
    tenant_id = reg.json()["tenant_id"]
    owner_token = reg.json()["access_token"]
    owner_headers = _headers("p5", owner_token)

    staff, staff_token = _make_staff_user(tenant_id, "p5", "staff@p5.test")

    role_id = client.post("/api/v1/roles", headers=owner_headers, json={"name": "Engineer"}).json()["id"]
    client.post(f"/api/v1/roles/{role_id}/permissions", headers=owner_headers, json={"permission_code": "engineering.act"})
    assign_resp = client.post(f"/api/v1/users/{staff.id}/roles", headers=owner_headers, json={"role_id": role_id})
    assert assign_resp.status_code == 200

    invoke_resp = client.post(
        "/api/v1/departments/engineering/invoke",
        headers=_headers("p5", staff_token),
        json={"prompt": "review this"},
    )
    # 503 (reached the AI Gateway, no provider configured) proves RBAC let
    # the request through -- a 403 here would mean the role assignment
    # didn't actually work.
    assert invoke_resp.status_code == 503


def test_list_users_shows_roles():
    reg = _register("p6")
    tenant_id = reg.json()["tenant_id"]
    owner_token = reg.json()["access_token"]
    owner_headers = _headers("p6", owner_token)
    staff, _ = _make_staff_user(tenant_id, "p6", "staff@p6.test")

    role_id = client.post("/api/v1/roles", headers=owner_headers, json={"name": "Engineer"}).json()["id"]
    client.post(f"/api/v1/users/{staff.id}/roles", headers=owner_headers, json={"role_id": role_id})

    resp = client.get("/api/v1/users", headers=owner_headers)
    assert resp.status_code == 200
    staff_entry = next(u for u in resp.json()["users"] if u["id"] == staff.id)
    assert staff_entry["roles"] == ["Engineer"]
