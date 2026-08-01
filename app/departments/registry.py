"""
The 26 AI departments from the master spec, as real, callable agents.

Each entry is an AgentCapability (system prompt, tools, escalation rules,
required permission). Executive Office keeps its bespoke subclass
(app/departments/executive_office/agent.py) because it has real custom
escalation logic; everything else runs through GenericDepartmentAgent,
which is a complete, working DepartmentAgent -- just driven by data
instead of a one-off class.

Escalation rules are genuinely differentiated per department (Phase 6),
not the same two generic conditions copy-pasted 25 times -- see
DEPARTMENT_ESCALATION_RULES below. They're evaluated by
DepartmentAgent.should_escalate() (app/departments/base/agent.py) and
wired into an automatic ApprovalRequest by
app/api/v1/departments_router.py's invoke handler, which is what turns
this from a data structure into something that actually protects the
founder from an AI department unilaterally committing to spend, legal
terms, or missing a security incident.

Permission codes follow "<department>.act" as the baseline gate; finer-
grained permissions (e.g. "finance.approve_payment" vs "finance.view_report")
are a Role/Permission-management concern (Phase 3), not a registry concern.
"""
from app.departments.base.agent import (
    ALWAYS_ESCALATE,
    AgentCapability,
    DepartmentAgent,
    EscalationRule,
    GenericDepartmentAgent,
)
from app.departments.executive_office.agent import ExecutiveOfficeAgent

# (registry key, department slug used in AI Gateway routing.yaml, display name, mission)
_DEPARTMENT_DEFINITIONS: list[tuple[str, str, str]] = [
    ("chief_of_staff", "Chief of Staff", "Coordinate priorities across all departments and prepare the founder for decisions."),
    ("administration", "Administration", "Handle scheduling, correspondence, and general administrative operations."),
    ("human_resources", "Human Resources", "Manage hiring processes, policies, and people operations for a company with one human employee (the founder) and many AI workers."),
    ("project_management_office", "Project Management Office", "Track project timelines, dependencies, and delivery risk across departments."),
    ("engineering", "Engineering", "Write, review, and maintain production code. Never merge or deploy without founder approval on anything touching production data."),
    ("product_management", "Product Management", "Define product requirements, prioritize the roadmap, and translate founder intent into specs."),
    ("devops", "DevOps", "Manage CI/CD, infrastructure, and deployments. Never provision paid cloud resources without founder approval."),
    ("ai_research", "AI Research", "Evaluate new models, providers, and techniques relevant to the company's AI Gateway and departments."),
    ("cyber_security", "Cyber Security", "Monitor for security risks, review code and infrastructure for vulnerabilities, and flag incidents immediately."),
    ("quality_assurance", "Quality Assurance", "Test features, review AI-generated work for correctness, and report defects before release."),
    ("sales", "Sales", "Manage the sales pipeline and outbound outreach. Never commit to pricing or contract terms without founder approval."),
    ("marketing", "Marketing", "Plan and draft marketing content and campaigns aligned with brand guidelines."),
    ("customer_success", "Customer Success", "Handle customer support, onboarding, and retention for tenant customers of the platform."),
    ("finance", "Finance", "Track spend, prepare financial summaries, and flag anomalies. Never authorize a payment without founder approval."),
    ("legal", "Legal", "Review contracts and flag legal risk. Never represents this as legal advice -- always recommends founder review by a licensed attorney for binding matters."),
    ("compliance", "Compliance", "Monitor regulatory and multi-tenant data-handling compliance (e.g. tenant data isolation, retention policy)."),
    ("internal_audit", "Internal Audit", "Independently review other departments' actions via the audit log for policy violations."),
    ("procurement", "Procurement", "Evaluate vendors and tools. Never commit to a paid subscription without founder approval."),
    ("operations", "Operations", "Coordinate day-to-day operational execution across departments."),
    ("analytics", "Analytics", "Analyze usage and performance data to surface trends for the founder."),
    ("business_intelligence", "Business Intelligence", "Synthesize cross-department data into strategic insight for the founder."),
    ("public_relations", "Public Relations", "Draft public-facing communications and monitor brand reputation."),
    ("investor_relations", "Investor Relations", "Prepare investor updates and materials. Never sends anything externally without founder approval."),
    ("knowledge_management", "Knowledge Management", "Maintain the company's internal knowledge base and documentation standards."),
    ("automation", "Automation", "Identify and build automations that reduce manual work across departments."),
]

_GENERIC_FINANCIAL_LEGAL = EscalationRule(
    condition="request or response mentions a financial or legal commitment",
    trigger_keywords=["payment", "contract", "spend", "budget", "sign", "nda", "invoice"],
)
_GENERIC_SECURITY = EscalationRule(
    condition="request or response mentions a security incident",
    trigger_keywords=["breach", "incident", "leaked", "unauthorized access", "compromise"],
)
_DEFAULT_RULES = [_GENERIC_FINANCIAL_LEGAL, _GENERIC_SECURITY]

# Departments with genuinely differentiated triggers beyond the generic
# baseline above. Anything not listed here falls back to _DEFAULT_RULES.
DEPARTMENT_ESCALATION_RULES: dict[str, list[EscalationRule]] = {
    "human_resources": [
        EscalationRule(
            condition="hiring, termination, or compensation action",
            trigger_keywords=["hire", "fire", "terminate", "layoff", "salary", "offer letter", "severance"],
        ),
    ],
    "engineering": [
        EscalationRule(
            condition="production or destructive data operation",
            trigger_keywords=["production", "deploy", "drop table", "delete database", "migrate data", "force push"],
        ),
    ],
    "devops": [
        EscalationRule(
            condition="production infrastructure or paid resource change",
            trigger_keywords=["provision", "deploy", "production", "delete", "terminate instance", "scale down"],
        ),
    ],
    "cyber_security": [
        EscalationRule(
            condition="active security incident indicator",
            trigger_keywords=["breach", "incident", "vulnerability", "leaked", "unauthorized", "exploit", "compromise"],
        ),
    ],
    "sales": [
        EscalationRule(
            condition="pricing or contract term commitment",
            trigger_keywords=["discount", "contract terms", "pricing exception", "commit to", "sign"],
        ),
    ],
    "finance": [
        EscalationRule(
            condition="payment, invoice, or budget commitment",
            trigger_keywords=["payment", "invoice", "refund", "wire transfer", "budget", "expense", "subscribe", "purchase"],
        ),
    ],
    "legal": [
        EscalationRule(
            condition="contract execution or legal exposure",
            trigger_keywords=["sign", "contract", "nda", "lawsuit", "liability", "terms of service", "legal action"],
        ),
    ],
    "compliance": [
        EscalationRule(
            condition="compliance violation or data-handling incident",
            trigger_keywords=["violation", "breach", "non-compliant", "data leak", "gdpr", "audit finding"],
        ),
    ],
    "internal_audit": [
        EscalationRule(
            condition="fraud or policy violation finding",
            trigger_keywords=["fraud", "violation", "policy breach", "irregularity"],
        ),
    ],
    "procurement": [
        EscalationRule(
            condition="new subscription or vendor commitment",
            trigger_keywords=["subscribe", "purchase", "vendor contract", "recurring payment", "sign"],
        ),
    ],
    "public_relations": [
        EscalationRule(
            condition="external public statement",
            trigger_keywords=["press release", "statement", "publish", "respond to media", "crisis"],
        ),
    ],
    "investor_relations": [
        EscalationRule(
            condition="any investor-facing communication (always escalated per department mission)",
            trigger_keywords=[ALWAYS_ESCALATE],
        ),
    ],
    "automation": [
        EscalationRule(
            condition="irreversible or bulk data operation",
            trigger_keywords=["delete", "production", "irreversible", "bulk update", "mass delete"],
        ),
    ],
    "customer_success": [
        EscalationRule(
            condition="refund or account credit",
            trigger_keywords=["refund", "compensation", "credit", "chargeback"],
        ),
    ],
    "marketing": [
        EscalationRule(
            condition="paid campaign or sponsorship spend",
            trigger_keywords=["spend", "budget", "paid campaign", "sponsorship"],
        ),
    ],
}

CAPABILITIES: dict[str, AgentCapability] = {
    "executive_office": ExecutiveOfficeAgent.capability,
}

for _slug, _name, _mission in _DEPARTMENT_DEFINITIONS:
    CAPABILITIES[_slug] = AgentCapability(
        department=_slug,
        task_type="default",
        system_prompt=(
            f"You are the {_name} AI for a single-founder autonomous company. "
            f"{_mission} Stay within your department's remit; escalate anything "
            f"involving spend, legal commitment, security incidents, or "
            f"strategic direction to the founder rather than acting on it."
        ),
        tools=[f"read_{_slug}_records"],
        escalation_rules=DEPARTMENT_ESCALATION_RULES.get(_slug, _DEFAULT_RULES),
        required_permission=f"{_slug}.act",
    )


def get_agent(department_slug: str, gateway) -> DepartmentAgent | None:
    """Returns a ready-to-run DepartmentAgent for the given slug, or None
    if the slug isn't a recognized department."""
    capability = CAPABILITIES.get(department_slug)
    if capability is None:
        return None
    if department_slug == "executive_office":
        return ExecutiveOfficeAgent(gateway)
    return GenericDepartmentAgent(gateway, capability)


def list_departments() -> list[dict]:
    return [
        {"slug": slug, "required_permission": cap.required_permission}
        for slug, cap in sorted(CAPABILITIES.items())
    ]
