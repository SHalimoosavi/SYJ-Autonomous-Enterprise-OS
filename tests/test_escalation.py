"""
Phase 6: escalation logic. Both the pure keyword-matching logic
(no AI/DB needed) and the wired-in behavior (an actual ApprovalRequest
gets created when a department's response would need founder sign-off).

Escalation is checked AFTER a successful agent.run() -- so these tests
need a real (fake) provider registered in the gateway, unlike most of
this project's department-invoke tests which rely on the "no provider
configured" 503 path. A FakeWorkingProvider is registered directly into
the gateway singleton for this file only.
"""
import asyncio

import pytest
from starlette.testclient import TestClient

from app.ai_gateway.gateway import get_gateway
from app.ai_gateway.providers.base import AIProvider, AIRequest, AIResponse
from app.core.database import Base, engine
from app.departments.base.agent import ALWAYS_ESCALATE, AgentCapability, EscalationRule, GenericDepartmentAgent
from app.departments.registry import CAPABILITIES, DEPARTMENT_ESCALATION_RULES
from app.main import app

client = TestClient(app)


class FakeEchoProvider(AIProvider):
    """Echoes the prompt back as the response, so tests can control
    exactly what text should_escalate() evaluates by controlling the
    prompt -- no real AI call, fully deterministic."""
    name = "fake_echo"

    async def generate(self, request: AIRequest, model: str) -> AIResponse:
        return AIResponse(text=f"echo: {request.prompt}", provider=self.name, model=model)

    async def health_check(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _fresh_schema_and_fake_provider(monkeypatch):
    async def _reset():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_reset())

    # Register a fake provider directly and monkeypatch every department's
    # routing so the gateway actually calls it -- simplest way to get a
    # real, controllable AIResponse without touching routing.yaml files
    # or requiring a real provider key. All three monkeypatch calls
    # revert automatically after each test, so this file can't leak a
    # "fake_echo" provider into the cached gateway singleton for tests
    # in other files that run afterward in the same session.
    gateway = get_gateway()
    monkeypatch.setitem(gateway._providers, "fake_echo", FakeEchoProvider())
    monkeypatch.setitem(
        gateway._routing, "default_fallback_chain", [{"provider": "fake_echo", "model": "test-model"}]
    )
    monkeypatch.setitem(gateway._routing, "departments", {})
    yield


def _register(tenant_slug):
    return client.post(
        "/api/v1/auth/register",
        json={"tenant_name": tenant_slug.title(), "tenant_slug": tenant_slug,
              "email": f"founder@{tenant_slug}.test", "password": "supersecret1"},
    )


def _headers(tenant_slug, token):
    return {"X-Tenant-ID": tenant_slug, "Authorization": f"Bearer {token}"}


# --- pure logic: should_escalate() ---

def test_should_escalate_matches_a_trigger_keyword():
    capability = AgentCapability(
        department="test_dept", task_type="default", system_prompt="",
        escalation_rules=[EscalationRule(condition="mentions payment", trigger_keywords=["payment", "invoice"])],
    )
    agent = GenericDepartmentAgent(gateway=None, capability=capability)
    rule = agent.should_escalate({"text": "Please process this payment for the vendor."})
    assert rule is not None
    assert rule.condition == "mentions payment"


def test_should_escalate_is_case_insensitive():
    capability = AgentCapability(
        department="test_dept", task_type="default", system_prompt="",
        escalation_rules=[EscalationRule(condition="x", trigger_keywords=["INVOICE"])],
    )
    agent = GenericDepartmentAgent(gateway=None, capability=capability)
    assert agent.should_escalate({"text": "please send the invoice"}) is not None


def test_should_escalate_returns_none_when_no_keywords_match():
    capability = AgentCapability(
        department="test_dept", task_type="default", system_prompt="",
        escalation_rules=[EscalationRule(condition="x", trigger_keywords=["payment"])],
    )
    agent = GenericDepartmentAgent(gateway=None, capability=capability)
    assert agent.should_escalate({"text": "what's the weather like today"}) is None


def test_always_escalate_sentinel_matches_any_text():
    capability = AgentCapability(
        department="test_dept", task_type="default", system_prompt="",
        escalation_rules=[EscalationRule(condition="always", trigger_keywords=[ALWAYS_ESCALATE])],
    )
    agent = GenericDepartmentAgent(gateway=None, capability=capability)
    assert agent.should_escalate({"text": "literally anything at all"}) is not None
    assert agent.should_escalate({"text": ""}) is not None


def test_investor_relations_always_escalates_by_registry_design():
    """Confirms the registry actually wires ALWAYS_ESCALATE for
    investor_relations, matching its "never sends anything externally
    without founder approval" mission statement."""
    capability = CAPABILITIES["investor_relations"]
    assert any(ALWAYS_ESCALATE in rule.trigger_keywords for rule in capability.escalation_rules)


def test_departments_have_genuinely_different_escalation_keywords():
    """Regression guard against reverting to the old copy-pasted-25-times
    generic rules."""
    finance_keywords = {kw for rule in CAPABILITIES["finance"].escalation_rules for kw in rule.trigger_keywords}
    hr_keywords = {kw for rule in CAPABILITIES["human_resources"].escalation_rules for kw in rule.trigger_keywords}
    assert finance_keywords != hr_keywords
    assert "invoice" in finance_keywords
    assert "terminate" in hr_keywords
    assert "invoice" not in hr_keywords


# --- wired-in behavior: real HTTP flow creates a real ApprovalRequest ---

def test_invoke_with_no_trigger_does_not_escalate():
    reg = _register("esc1")
    token = reg.json()["access_token"]
    resp = client.post(
        "/api/v1/departments/engineering/invoke",
        headers=_headers("esc1", token),
        json={"prompt": "What's a good code review checklist?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["escalated"] is False
    assert body["escalation"] is None


def test_invoke_with_trigger_keyword_escalates_and_creates_approval():
    reg = _register("esc2")
    token = reg.json()["access_token"]
    headers = _headers("esc2", token)

    resp = client.post(
        "/api/v1/departments/engineering/invoke",
        headers=headers,
        json={"prompt": "Please deploy this change to production right now."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["escalated"] is True
    assert body["escalation"]["approval_id"]
    assert "production" in body["escalation"]["reason"] or "deploy" in body["escalation"]["reason"]

    # The approval must be real and visible through the normal Approval
    # Queue API, not just claimed in the invoke response.
    approvals = client.get("/api/v1/approvals", headers=headers).json()["approvals"]
    assert any(a["id"] == body["escalation"]["approval_id"] for a in approvals)
    escalated = next(a for a in approvals if a["id"] == body["escalation"]["approval_id"])
    assert escalated["department"] == "engineering"
    assert escalated["status"] == "pending"


def test_investor_relations_always_escalates_via_real_http_call():
    reg = _register("esc3")
    token = reg.json()["access_token"]
    resp = client.post(
        "/api/v1/departments/investor_relations/invoke",
        headers=_headers("esc3", token),
        json={"prompt": "Draft a quick note to send to investors."},
    )
    assert resp.status_code == 200
    assert resp.json()["escalated"] is True


def test_escalation_is_audit_logged():
    from sqlalchemy import select
    from app.audit.logger import AuditLog
    from app.core.database import AsyncSessionLocal

    reg = _register("esc4")
    token = reg.json()["access_token"]
    client.post(
        "/api/v1/departments/finance/invoke",
        headers=_headers("esc4", token),
        json={"prompt": "Approve this invoice for payment."},
    )

    async def _check():
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(AuditLog).where(AuditLog.action == "finance.escalated"))
            return result.scalar_one_or_none()

    entry = asyncio.run(_check())
    assert entry is not None
