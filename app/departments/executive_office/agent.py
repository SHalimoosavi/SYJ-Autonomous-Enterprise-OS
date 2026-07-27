"""
Executive Office department — reference implementation showing how a real
department is built on top of the base agent + AI Gateway. Produces the
CEO Briefing consumed by the dashboard.
"""
from app.departments.base.agent import AgentCapability, DepartmentAgent, EscalationRule


class ExecutiveOfficeAgent(DepartmentAgent):
    capability = AgentCapability(
        department="executive_office",
        task_type="briefing_synthesis",
        system_prompt=(
            "You are the Executive Office AI for a single-founder autonomous "
            "company. Synthesize department reports into a concise CEO "
            "briefing: key decisions needed, risks, and wins. Never make "
            "financial, legal, or strategic commitments — flag them for "
            "founder approval instead."
        ),
        tools=["read_department_reports", "read_kpi_snapshot", "read_approval_queue"],
        escalation_rules=[
            EscalationRule(condition="any department reports a security incident", escalate_to="ceo"),
            EscalationRule(condition="financial commitment referenced in report", escalate_to="ceo"),
        ],
        required_permission="executive.view_briefing",
    )

    def should_escalate(self, context: dict) -> EscalationRule | None:
        if context.get("security_incident"):
            return self.capability.escalation_rules[0]
        if context.get("financial_commitment"):
            return self.capability.escalation_rules[1]
        return None
