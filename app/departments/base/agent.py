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

ALWAYS_ESCALATE = "*"  # sentinel trigger_keywords value meaning "every invocation"


@dataclass
class EscalationRule:
    condition: str          # human-readable trigger, e.g. "spend > $1000" -- shown to the founder in the approval
    escalate_to: str = "ceo"
    # Keyword match against the combined prompt+response text (lowercased,
    # substring match). ALWAYS_ESCALATE ("*") means every invocation of
    # this department escalates regardless of content -- used for
    # departments where the master spec says "never sends anything
    # externally without founder approval" rather than a content-based
    # trigger (e.g. Investor Relations). See DepartmentAgent.should_escalate
    # for how this is evaluated; this is what turned the previously-inert
    # EscalationRule/should_escalate design from Phase 1 into something
    # actually wired to app/api/v1/departments_router.py's invoke handler.
    trigger_keywords: list[str] = field(default_factory=list)


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
        """
        Default, data-driven escalation: checks context["text"] (expected
        to be the prompt + response, combined by the caller) against each
        rule's trigger_keywords, case-insensitive substring match. Returns
        the first matching rule, or None if nothing matches.

        This is deliberately keyword-based rather than a second AI call
        to "judge" whether escalation is warranted: it's fast, free,
        fully deterministic, and testable without any AI provider being
        configured -- all real properties a safety-relevant check should
        have. It will have false negatives (a risky request phrased
        without any trigger word) and the occasional false positive
        (an incidental keyword match) -- a known, accepted tradeoff for
        a first pass, not a claim of perfect judgment. Subclasses (see
        ExecutiveOfficeAgent) can still override this entirely for
        genuinely custom logic beyond keyword matching.
        """
        text = (context.get("text") or "").lower()
        for rule in self.capability.escalation_rules:
            if ALWAYS_ESCALATE in rule.trigger_keywords:
                return rule
            if any(keyword.lower() in text for keyword in rule.trigger_keywords):
                return rule
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
