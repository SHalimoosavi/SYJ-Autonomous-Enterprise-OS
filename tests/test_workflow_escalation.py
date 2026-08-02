"""
Phase 7: workflow-level escalation. A step whose response matches an
escalation rule must pause the whole run (not silently continue into
later steps that would chain off the escalated content), create a real
ApprovalRequest, and be resumable once that approval is decided.
"""
import asyncio

import pytest
from starlette.testclient import TestClient

from app.ai_gateway.gateway import get_gateway
from app.ai_gateway.providers.base import AIProvider, AIRequest, AIResponse
from app.core.database import Base, engine
from app.main import app

client = TestClient(app)


class ScriptedProvider(AIProvider):
    """Returns a different canned response per call, in order -- lets a
    test script exactly what each workflow step "says" so escalation
    triggers on a specific step deterministically."""
    name = "scripted"

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.call_count = 0

    async def generate(self, request: AIRequest, model: str) -> AIResponse:
        text = self._responses[min(self.call_count, len(self._responses) - 1)]
        self.call_count += 1
        return AIResponse(text=text, provider=self.name, model=model)

    async def health_check(self) -> bool:
        return True


def _wire_scripted_provider(monkeypatch, responses):
    gateway = get_gateway()
    provider = ScriptedProvider(responses)
    monkeypatch.setitem(gateway._providers, "scripted", provider)
    monkeypatch.setitem(gateway._routing, "default_fallback_chain", [{"provider": "scripted", "model": "test-model"}])
    monkeypatch.setitem(gateway._routing, "departments", {})
    return provider


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


def test_workflow_with_no_escalating_step_completes_normally(monkeypatch):
    _wire_scripted_provider(monkeypatch, ["Looks fine, no concerns.", "QA approves.", "DevOps ready to ship."])
    reg = _register("we1")
    token = reg.json()["access_token"]
    resp = client.post(
        "/api/v1/workflows/release_review/run", headers=_headers("we1", token), json={"input": "Add a health check"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert len(resp.json()["steps"]) == 3


def test_workflow_pauses_at_escalating_step_and_does_not_run_later_steps(monkeypatch):
    provider = _wire_scripted_provider(
        monkeypatch,
        ["We should deploy this straight to production immediately.", "QA step should never run", "DevOps step should never run"],
    )
    reg = _register("we2")
    token = reg.json()["access_token"]
    headers = _headers("we2", token)

    resp = client.post("/api/v1/workflows/release_review/run", headers=headers, json={"input": "Ship the hotfix"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "escalated"
    assert body["escalated_step"] == "engineering"
    assert body["approval_id"]
    # Only the first (escalating) step actually ran -- QA/DevOps never called.
    assert provider.call_count == 1

    run_id = body["run_id"]
    run_detail = client.get(f"/api/v1/workflows/runs/{run_id}", headers=headers).json()
    assert run_detail["status"] == "escalated"
    assert run_detail["pending_approval_id"] == body["approval_id"]
    assert len(run_detail["steps"]) == 1
    assert run_detail["steps"][0]["escalated"] is True


def test_escalated_workflow_creates_a_real_approval_queue_entry(monkeypatch):
    _wire_scripted_provider(monkeypatch, ["Deploy this to production now."])
    reg = _register("we3")
    token = reg.json()["access_token"]
    headers = _headers("we3", token)

    resp = client.post("/api/v1/workflows/release_review/run", headers=headers, json={"input": "Ship it"})
    approval_id = resp.json()["approval_id"]

    approvals = client.get("/api/v1/approvals", headers=headers).json()["approvals"]
    assert any(a["id"] == approval_id for a in approvals)
    matching = next(a for a in approvals if a["id"] == approval_id)
    assert matching["status"] == "pending"
    assert matching["department"] == "engineering"


def test_resume_before_approval_is_rejected(monkeypatch):
    _wire_scripted_provider(monkeypatch, ["Deploy to production now."])
    reg = _register("we4")
    token = reg.json()["access_token"]
    headers = _headers("we4", token)

    resp = client.post("/api/v1/workflows/release_review/run", headers=headers, json={"input": "Ship it"})
    run_id = resp.json()["run_id"]

    resume = client.post(f"/api/v1/workflows/runs/{run_id}/resume", headers=headers)
    assert resume.status_code == 409
    assert "not approved" in resume.json()["detail"].lower()


def test_resume_after_approval_continues_from_the_next_step(monkeypatch):
    provider = _wire_scripted_provider(
        monkeypatch,
        ["Deploy to production now.", "QA confirms tests pass.", "DevOps rollback plan ready."],
    )
    reg = _register("we5")
    token = reg.json()["access_token"]
    headers = _headers("we5", token)

    started = client.post("/api/v1/workflows/release_review/run", headers=headers, json={"input": "Ship it"})
    run_id = started.json()["run_id"]
    approval_id = started.json()["approval_id"]
    assert provider.call_count == 1

    decide = client.post(f"/api/v1/approvals/{approval_id}/decide", headers=headers, json={"decision": "approved"})
    assert decide.status_code == 200

    resumed = client.post(f"/api/v1/workflows/runs/{run_id}/resume", headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "completed"
    # The remaining two steps (QA, DevOps) actually ran -- not re-running
    # the already-completed engineering step.
    assert provider.call_count == 3

    full_run = client.get(f"/api/v1/workflows/runs/{run_id}", headers=headers).json()
    assert full_run["status"] == "completed"
    assert len(full_run["steps"]) == 3
    assert [s["department"] for s in full_run["steps"]] == ["engineering", "quality_assurance", "devops"]


def test_resume_after_rejection_is_still_blocked(monkeypatch):
    _wire_scripted_provider(monkeypatch, ["Deploy to production now."])
    reg = _register("we6")
    token = reg.json()["access_token"]
    headers = _headers("we6", token)

    started = client.post("/api/v1/workflows/release_review/run", headers=headers, json={"input": "Ship it"})
    run_id = started.json()["run_id"]
    approval_id = started.json()["approval_id"]

    client.post(f"/api/v1/approvals/{approval_id}/decide", headers=headers, json={"decision": "rejected"})

    resume = client.post(f"/api/v1/workflows/runs/{run_id}/resume", headers=headers)
    assert resume.status_code == 409


def test_resume_unknown_run_returns_404():
    reg = _register("we7")
    token = reg.json()["access_token"]
    resp = client.post(
        "/api/v1/workflows/runs/00000000-0000-0000-0000-000000000000/resume", headers=_headers("we7", token)
    )
    assert resp.status_code == 404


def test_resume_on_a_non_escalated_run_returns_409(monkeypatch):
    _wire_scripted_provider(monkeypatch, ["All clear.", "QA fine.", "DevOps fine."])
    reg = _register("we8")
    token = reg.json()["access_token"]
    headers = _headers("we8", token)

    completed = client.post("/api/v1/workflows/release_review/run", headers=headers, json={"input": "x"})
    run_id = completed.json()["run_id"]

    resume = client.post(f"/api/v1/workflows/runs/{run_id}/resume", headers=headers)
    assert resume.status_code == 409


def test_escalation_audit_logged_for_workflow(monkeypatch):
    from sqlalchemy import select
    from app.audit.logger import AuditLog
    from app.core.database import AsyncSessionLocal

    _wire_scripted_provider(monkeypatch, ["Deploy to production now."])
    reg = _register("we9")
    token = reg.json()["access_token"]
    client.post("/api/v1/workflows/release_review/run", headers=_headers("we9", token), json={"input": "Ship it"})

    async def _check():
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(AuditLog).where(AuditLog.action == "workflow.escalated"))
            return result.scalar_one_or_none()

    assert asyncio.run(_check()) is not None
