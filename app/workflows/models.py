"""
WorkflowRun / WorkflowStepRun -- persisted history of multi-step,
multi-department orchestrations. Execution itself is synchronous within
the request (see router.py's module docstring for why); these tables
exist so a completed, failed, or escalated run can be inspected
afterward, and so a future async/Celery-backed executor can write to the
exact same shape without a schema change.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Text, Enum, ForeignKey, Integer, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    # A step's response matched an escalation rule (see
    # app/departments/base/agent.py's should_escalate). The run stops
    # here -- deliberately not "flag and continue" the way single
    # department-invoke escalation works (Phase 6): a multi-step workflow
    # chains each step's output into the next step's prompt via
    # {previous}, so letting execution continue past an escalated step
    # would mean further AI-generated content builds on something that
    # hasn't had founder sign-off yet. Resume via
    # POST /api/v1/workflows/runs/{id}/resume once the linked
    # ApprovalRequest is approved.
    ESCALATED = "escalated"


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)

    workflow_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    started_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.RUNNING)
    input_prompt: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when status == ESCALATED; the ApprovalRequest that must be
    # approved before /resume will proceed past the step that triggered it.
    pending_approval_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("approval_requests.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowStepRun(Base):
    __tablename__ = "workflow_step_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("workflow_runs.id"), nullable=False, index=True)

    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.RUNNING)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
