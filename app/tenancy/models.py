"""Tenant model — the root of data isolation for the multi-tenant SaaS."""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Enum, func
from sqlalchemy.orm import Mapped, mapped_column
import enum

from app.core.database import Base


class TenantPlan(str, enum.Enum):
    SELF_HOSTED = "self_hosted"
    WHITE_LABEL = "white_label"
    SUBSCRIPTION_STARTER = "subscription_starter"
    SUBSCRIPTION_PRO = "subscription_pro"
    SUBSCRIPTION_ENTERPRISE = "subscription_enterprise"


class TenantStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"
    CANCELLED = "cancelled"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)  # subdomain / white-label key
    plan: Mapped[TenantPlan] = mapped_column(Enum(TenantPlan), default=TenantPlan.SUBSCRIPTION_STARTER)
    status: Mapped[TenantStatus] = mapped_column(Enum(TenantStatus), default=TenantStatus.TRIAL)

    # White-label branding overrides, resolved by the frontend per tenant
    brand_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand_logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    brand_primary_color: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Per-tenant AI provider keys, isolated per customer (encrypted at rest — see security review)
    ai_provider_overrides_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
