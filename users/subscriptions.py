"""Subscription gating primitives for future SaaS mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SubscriptionPlan(str, Enum):
    FREE = "free"
    PRO = "pro"
    DESK = "desk"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"


@dataclass(slots=True)
class Subscription:
    tenant_id: str
    plan: str = SubscriptionPlan.FREE.value
    status: str = SubscriptionStatus.TRIALING.value
    current_period_end: datetime | None = None
    features: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_active(self) -> bool:
        if self.status not in {SubscriptionStatus.TRIALING.value, SubscriptionStatus.ACTIVE.value}:
            return False
        if self.current_period_end and self.current_period_end < datetime.utcnow():
            return False
        return True

    def allows(self, feature: str) -> bool:
        if not self.is_active():
            return False
        if self.plan in {SubscriptionPlan.DESK.value, SubscriptionPlan.ENTERPRISE.value}:
            return True
        return feature in self.features


class SubscriptionGate:
    """Small policy object used later by route decorators and bot launch flows."""

    def __init__(self, saas_mode: bool = False):
        self.saas_mode = saas_mode

    def can_use_feature(self, subscription: Subscription | None, feature: str) -> bool:
        if not self.saas_mode:
            return True
        return bool(subscription and subscription.allows(feature))

