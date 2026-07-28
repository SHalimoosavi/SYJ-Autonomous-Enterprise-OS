"""
Base class every department AI worker inherits from. Encodes the contract:
tools, memory, permissions, and escalation rules are declared here, not
scattered per-department — so the Executive Office, Finance, Engineering,
etc. agents all look structurally identical to the platform even though
their prompts/tools differ.
"""
from abc import ABC
from dataclasses import dataclass, field

from app.ai_gateway.gateway import AIGateway
from app.ai_gateway.providers.base import AIRequest, AIResponse


@dataclass
class EscalationRule:
    condition: str          # human-readable trigger, e.g. "spend > $1000"
    escalate_to: str = "ceo"


@dataclass
class AgentCapability:
    department: str
    task_type: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    escalation_rules: list[EscalationRule] = field(default_factory=list)
    required_permission: str | None = None


class DepartmentAgent(ABC):
    capability: AgentCapability

    def __init__(self, gateway: AIGateway):
        self.gateway = gateway

    async def run(self, user_prompt: str, tenant_id: str) -> AIResponse:
        request = AIRequest(prompt=user_prompt, system=self.capability.system_prompt)
        return await self.gateway.generate(
            department=self.capability.department,
            task_type=self.capability.task_type,
            request=request,
        )

    def should_escalate(self, context: dict) -> EscalationRule | None:
        """Override per-agent with real logic; base returns None (no escalation)."""
        return None


class GenericDepartmentAgent(DepartmentAgent):
    """
    A DepartmentAgent driven entirely by a capability definition, rather
    than a bespoke subclass. This is what lets all 26 departments from the
    master spec exist as real, callable agents (see
    app/departments/registry.py) without 26 near-identical Python files --
    each one is a few lines of data. Departments with genuinely custom
    logic (see ExecutiveOfficeAgent's should_escalate override) still get
    a real subclass; this is the default for everything else.
    """

    def __init__(self, gateway: AIGateway, capability: AgentCapability):
        super().__init__(gateway)
        self.capability = capability
