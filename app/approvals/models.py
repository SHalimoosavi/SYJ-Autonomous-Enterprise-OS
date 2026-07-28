"""
Approval Queue -- the concrete implementation of "the CEO approves
strategic decisions, financial commitments, legal matters, and
exceptions" from the master spec's Human Role section. Any department
agent's should_escalate() is meant to produce one of these rather than
act unilaterally; for now they're also creatable directly via the API
for department agents/integrations that don't go through should_escalate.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Enum, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)

    department: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    requested_by: Mapped[str] = mapped_column(String(150), nullable=False)  # user id or agent/department slug

    status: Mapped[ApprovalStatus] = mapped_column(Enum(ApprovalStatus), default=ApprovalStatus.PENDING)
    decided_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
