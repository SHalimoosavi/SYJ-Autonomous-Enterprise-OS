"""
Append-only audit log. Every privileged action (auth, RBAC denial, AI
gateway call, approval, escalation) writes here. Required for compliance
department + enterprise customers' audit needs.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)   # user id or "system"/agent name
    action: Mapped[str] = mapped_column(String(150), nullable=False)    # e.g. "ai_gateway.generate"
    resource: Mapped[str] = mapped_column(String(255), default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
