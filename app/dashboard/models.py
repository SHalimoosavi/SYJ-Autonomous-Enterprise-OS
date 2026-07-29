"""
Real, DB-backed data sources for the dashboard widgets that Phase 2/3
explicitly deferred ("need real department data sources first"). These
are intentionally simple, general-purpose models -- a KPI is just a
named metric with a value and a timestamp, a sales deal has a stage, a
transaction has a category and amount -- rather than elaborate
department-specific schemas, since no department has generated real
business data yet. Departments (or the founder) record into these via
the endpoints in router.py; the dashboard aggregates them.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Enum, Numeric, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class KPIMetric(Base):
    __tablename__ = "kpi_metrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)

    department: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(150), nullable=False)  # e.g. "mrr", "active_users"
    value: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DealStage(str, enum.Enum):
    LEAD = "lead"
    QUALIFIED = "qualified"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"


class SalesDeal(Base):
    __tablename__ = "sales_deals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[DealStage] = mapped_column(Enum(DealStage), default=DealStage.LEAD)
    value: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TransactionType(str, enum.Enum):
    INCOME = "income"
    EXPENSE = "expense"


class FinancialTransaction(Base):
    __tablename__ = "financial_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)

    type: Mapped[TransactionType] = mapped_column(Enum(TransactionType), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "hosting", "revenue"
    amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")

    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
