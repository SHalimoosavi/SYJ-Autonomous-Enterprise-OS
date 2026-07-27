"""User, Role, Permission — scoped per tenant for RBAC."""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, Table, Column, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

role_permissions = Table(
    "role_permissions", Base.metadata,
    Column("role_id", String(36), ForeignKey("roles.id"), primary_key=True),
    Column("permission_id", String(36), ForeignKey("permissions.id"), primary_key=True),
)

user_roles = Table(
    "user_roles", Base.metadata,
    Column("user_id", String(36), ForeignKey("users.id"), primary_key=True),
    Column("role_id", String(36), ForeignKey("roles.id"), primary_key=True),
)


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)  # e.g. "finance.approve_payment"
    description: Mapped[str] = mapped_column(String(255), default="")


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "CEO", "Platform Admin"
    permissions: Mapped[list[Permission]] = relationship(secondary=role_permissions)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    # Cross-tenant access for you, the OS operator (support, billing, ops).
    is_platform_admin: Mapped[bool] = mapped_column(default=False)
    # Full access within their own tenant only, without needing individual
    # permission rows -- set on the user who completes /auth/register for
    # a new tenant. Distinct from is_platform_admin, which is cross-tenant.
    is_tenant_owner: Mapped[bool] = mapped_column(default=False)

    roles: Mapped[list[Role]] = relationship(secondary=user_roles)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
