"""Filesystem isolation helpers for future multi-tenant bot state."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from users.auth import current_user_context, is_saas_mode


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TENANT_DATA_DIR = os.path.join(BASE_DIR, "data", "tenants")


def tenant_safe_name(value: str | None) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "local")).strip(".-")
    return cleaned[:80] or "local"


def get_active_tenant_id(default: str = "local") -> str:
    if not is_saas_mode():
        return default
    context = current_user_context()
    return tenant_safe_name(context.tenant_id or default)


@dataclass(frozen=True, slots=True)
class TenantPaths:
    tenant_id: str
    root: str
    config_path: str
    mt5_credentials_path: str
    alerts_path: str
    journal_path: str
    trades_path: str

    def ensure(self) -> "TenantPaths":
        os.makedirs(self.root, exist_ok=True)
        return self


def get_tenant_paths(tenant_id: str | None = None, create: bool = False) -> TenantPaths:
    safe_tenant_id = tenant_safe_name(tenant_id or get_active_tenant_id())
    root = os.path.join(TENANT_DATA_DIR, safe_tenant_id)
    paths = TenantPaths(
        tenant_id=safe_tenant_id,
        root=root,
        config_path=os.path.join(root, "config.json"),
        mt5_credentials_path=os.path.join(root, "mt5_credentials.json"),
        alerts_path=os.path.join(root, "alerts.jsonl"),
        journal_path=os.path.join(root, "strategy_journal.jsonl"),
        trades_path=os.path.join(root, "trades.csv"),
    )
    return paths.ensure() if create else paths

