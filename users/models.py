"""Lightweight user models for future SaaS mode.

These models intentionally avoid database coupling so the current local bot can
keep running without migrations or login requirements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


ROLE_ADMIN = "admin"
ROLE_TRADER = "trader"
ROLE_VIEWER = "viewer"
VALID_ROLES = {ROLE_ADMIN, ROLE_TRADER, ROLE_VIEWER}


class UserRole(str, Enum):
    ADMIN = ROLE_ADMIN
    TRADER = ROLE_TRADER
    VIEWER = ROLE_VIEWER


@dataclass(slots=True)
class User:
    id: str
    email: str
    role: str = ROLE_TRADER
    tenant_id: str = "local"
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    def can_trade(self) -> bool:
        return self.is_active and self.role in {ROLE_ADMIN, ROLE_TRADER}

    def can_admin(self) -> bool:
        return self.is_active and self.role == ROLE_ADMIN

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "role": self.role,
            "tenant_id": self.tenant_id,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class TenantUserContext:
    user: User | None
    tenant_id: str = "local"
    saas_mode: bool = False

    @property
    def is_authenticated(self) -> bool:
        return not self.saas_mode or (self.user is not None and self.user.is_active)

    @property
    def role(self) -> str:
        return self.user.role if self.user else ROLE_ADMIN

