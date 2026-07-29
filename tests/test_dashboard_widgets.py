"""
Phase 4 dashboard widgets: KPIs, sales pipeline, financial summary. Pure
DB CRUD + aggregation, zero AI Gateway dependency, so unlike most of this
project's endpoints these have no external-service graceful-failure path
to test -- just correctness.
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


def _make_staff(tenant_id, tenant_slug, email):
    from app.auth.models import User
    from app.core.database import AsyncSessionLocal
    from app.core.security import create_access_token, hash_password

    async def _make():
        async with AsyncSessionLocal() as session:
            user = User(tenant_id=tenant_id, email=email, hashed_password=hash_password("staffpassword1"),
                        is_platform_admin=False, is_tenant_owner=False)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    user = asyncio.run(_make())
    return user, create_access_token(subject=user.id, tenant_id=tenant_id)


# --- KPIs ---

def test_record_and_list_kpi():
    reg = _register("d1")
    token = reg.json()["access_token"]
    headers = _headers("d1", token)

    resp = client.post("/api/v1/dashboard/kpis", headers=headers,
                        json={"department": "sales", "metric_name": "mrr", "value": 4200})
    assert resp.status_code == 201
    assert resp.json()["value"] == 4200.0

    listing = client.get("/api/v1/dashboard/kpis", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["metrics"][0]["metric_name"] == "mrr"


def test_record_kpi_requires_numeric_value():
    reg = _register("d2")
    token = reg.json()["access_token"]
    resp = client.post("/api/v1/dashboard/kpis", headers=_headers("d2", token),
                        json={"department": "sales", "metric_name": "mrr", "value": "not-a-number"})
    assert resp.status_code == 400


def test_kpi_list_filters_by_department():
    reg = _register("d3")
    token = reg.json()["access_token"]
    headers = _headers("d3", token)
    client.post("/api/v1/dashboard/kpis", headers=headers, json={"department": "sales", "metric_name": "mrr", "value": 1})
    client.post("/api/v1/dashboard/kpis", headers=headers, json={"department": "engineering", "metric_name": "uptime", "value": 99.9})

    resp = client.get("/api/v1/dashboard/kpis?department=engineering", headers=headers)
    metrics = resp.json()["metrics"]
    assert len(metrics) == 1
    assert metrics[0]["department"] == "engineering"


# --- sales pipeline ---

def test_create_deal_and_update_stage():
    reg = _register("d4")
    token = reg.json()["access_token"]
    headers = _headers("d4", token)

    created = client.post("/api/v1/dashboard/pipeline", headers=headers, json={"name": "Acme Corp deal", "value": 15000})
    assert created.status_code == 201
    assert created.json()["stage"] == "lead"
    deal_id = created.json()["id"]

    updated = client.post(f"/api/v1/dashboard/pipeline/{deal_id}/stage", headers=headers, json={"stage": "won"})
    assert updated.status_code == 200
    assert updated.json()["stage"] == "won"


def test_update_deal_stage_rejects_invalid_stage():
    reg = _register("d5")
    token = reg.json()["access_token"]
    headers = _headers("d5", token)
    deal_id = client.post("/api/v1/dashboard/pipeline", headers=headers, json={"name": "X", "value": 100}).json()["id"]
    resp = client.post(f"/api/v1/dashboard/pipeline/{deal_id}/stage", headers=headers, json={"stage": "not_a_stage"})
    assert resp.status_code == 400


def test_pipeline_aggregation_by_stage():
    reg = _register("d6")
    token = reg.json()["access_token"]
    headers = _headers("d6", token)
    d1 = client.post("/api/v1/dashboard/pipeline", headers=headers, json={"name": "Deal 1", "value": 1000}).json()["id"]
    client.post("/api/v1/dashboard/pipeline", headers=headers, json={"name": "Deal 2", "value": 2000})
    client.post(f"/api/v1/dashboard/pipeline/{d1}/stage", headers=headers, json={"stage": "won"})

    resp = client.get("/api/v1/dashboard/pipeline", headers=headers)
    by_stage = resp.json()["by_stage"]
    assert by_stage["won"]["count"] == 1
    assert by_stage["won"]["total_value"] == 1000.0
    assert by_stage["lead"]["count"] == 1


# --- financial summary ---

def test_record_transaction_requires_owner():
    reg = _register("d7")
    tenant_id = reg.json()["tenant_id"]
    _, staff_token = _make_staff(tenant_id, "d7", "staff@d7.test")
    resp = client.post("/api/v1/dashboard/finance", headers=_headers("d7", staff_token),
                        json={"type": "income", "category": "sales", "amount": 500})
    assert resp.status_code == 403


def test_financial_summary_computes_net():
    reg = _register("d8")
    token = reg.json()["access_token"]
    headers = _headers("d8", token)

    client.post("/api/v1/dashboard/finance", headers=headers, json={"type": "income", "category": "sales", "amount": 1000})
    client.post("/api/v1/dashboard/finance", headers=headers, json={"type": "expense", "category": "hosting", "amount": 300})

    resp = client.get("/api/v1/dashboard/finance", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_income"] == 1000.0
    assert body["total_expense"] == 300.0
    assert body["net"] == 700.0


def test_invalid_transaction_type_rejected():
    reg = _register("d9")
    token = reg.json()["access_token"]
    resp = client.post("/api/v1/dashboard/finance", headers=_headers("d9", token),
                        json={"type": "not_a_type", "category": "x", "amount": 1})
    assert resp.status_code == 400


# --- enriched CEO briefing ---

def test_briefing_includes_pipeline_and_financial_numbers():
    reg = _register("d10")
    token = reg.json()["access_token"]
    headers = _headers("d10", token)

    client.post("/api/v1/dashboard/pipeline", headers=headers, json={"name": "Deal", "value": 5000})
    client.post("/api/v1/dashboard/finance", headers=headers, json={"type": "income", "category": "sales", "amount": 2000})
    client.post("/api/v1/dashboard/finance", headers=headers, json={"type": "expense", "category": "hosting", "amount": 500})

    resp = client.get("/api/v1/dashboard/briefing", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["open_pipeline_value"] == 5000.0
    assert body["net_financial_position"] == 1500.0
