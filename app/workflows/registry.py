"""
Workflow definitions: named sequences of department steps. Each step's
output prompt template can reference {input} (the workflow's original
input prompt) and {previous} (the immediately preceding step's output),
so steps genuinely chain rather than just running in parallel isolation.

These two are real, useful sequences built from Phase 2's department
registry -- not placeholders. More workflows are just more entries here;
no engine changes needed, same "data over code" pattern as the
department registry itself.
"""
from dataclasses import dataclass


@dataclass
class WorkflowStepDef:
    department: str
    prompt_template: str  # may reference {input} and {previous}


@dataclass
class WorkflowDef:
    slug: str
    name: str
    steps: list[WorkflowStepDef]


WORKFLOWS: dict[str, WorkflowDef] = {
    "release_review": WorkflowDef(
        slug="release_review",
        name="Release Review",
        steps=[
            WorkflowStepDef(
                department="engineering",
                prompt_template="Review this change for correctness and risk before release: {input}",
            ),
            WorkflowStepDef(
                department="quality_assurance",
                prompt_template=(
                    "Engineering's review of the change was:\n{previous}\n\n"
                    "Given that, what should QA specifically test before this ships? Original change: {input}"
                ),
            ),
            WorkflowStepDef(
                department="devops",
                prompt_template=(
                    "Engineering and QA have reviewed this change:\n{previous}\n\n"
                    "What deployment steps or rollback plan does DevOps need for: {input}"
                ),
            ),
        ],
    ),
    "vendor_onboarding": WorkflowDef(
        slug="vendor_onboarding",
        name="Vendor Onboarding",
        steps=[
            WorkflowStepDef(
                department="procurement",
                prompt_template="Evaluate this vendor for onboarding: {input}",
            ),
            WorkflowStepDef(
                department="legal",
                prompt_template=(
                    "Procurement's evaluation was:\n{previous}\n\n"
                    "What legal/contract risks should be flagged for founder review before signing: {input}"
                ),
            ),
            WorkflowStepDef(
                department="finance",
                prompt_template=(
                    "Procurement and Legal have reviewed this vendor:\n{previous}\n\n"
                    "What budget/payment-term considerations apply for: {input}"
                ),
            ),
        ],
    ),
}


def list_workflows() -> list[dict]:
    return [{"slug": w.slug, "name": w.name, "steps": len(w.steps)} for w in WORKFLOWS.values()]


def get_workflow(slug: str) -> WorkflowDef | None:
    return WORKFLOWS.get(slug)
