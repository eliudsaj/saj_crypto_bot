"""Authentication scaffolding for optional SaaS mode.

Local mode remains permissive. When SAAS_MODE is enabled, these helpers provide
the place to attach session/JWT validation without changing bot internals.
"""

from __future__ import annotations

import os
import hmac
from functools import wraps
from typing import Callable

from flask import jsonify, request, session

from .models import ROLE_ADMIN, ROLE_TRADER, ROLE_VIEWER, TenantUserContext, User, VALID_ROLES
from .store import get_user_store


ROLE_PERMISSIONS = {
    ROLE_ADMIN: {
        "read",
        "trade",
        "panic",
        "settings",
        "users",
        "licenses",
        "brokers",
    },
    ROLE_TRADER: {
        "read",
        "trade",
        "panic",
    },
    ROLE_VIEWER: {
        "read",
    },
}


def is_saas_mode() -> bool:
    return os.getenv("SAAS_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}


def _admin_email() -> str:
    return os.getenv("SAAS_ADMIN_EMAIL", "admin@nexus.local").strip().lower()


def _admin_password() -> str:
    return os.getenv("SAAS_ADMIN_PASSWORD", "change-me-now")


def authenticate_user(email: str, password: str) -> TenantUserContext | None:
    """Authenticate a stored SaaS user, falling back to bootstrap admin env."""
    email = str(email or "").strip().lower()
    password = str(password or "")
    if not email or not password:
        return None
    stored_user = get_user_store().authenticate(email, password)
    if stored_user:
        return TenantUserContext(
            user=stored_user,
            tenant_id=stored_user.tenant_id,
            saas_mode=True,
        )
    if not hmac.compare_digest(email, _admin_email()):
        return None
    if not hmac.compare_digest(password, _admin_password()):
        return None
    tenant_id = os.getenv("SAAS_DEFAULT_TENANT", "default").strip() or "default"
    return TenantUserContext(
        user=User(id="bootstrap-admin", email=email, role=ROLE_ADMIN, tenant_id=tenant_id),
        tenant_id=tenant_id,
        saas_mode=True,
    )


def login_context(context: TenantUserContext) -> None:
    if not context.user:
        return
    session["nexus_user"] = {
        "id": context.user.id,
        "email": context.user.email,
        "role": context.user.role,
        "tenant_id": context.tenant_id,
    }


def logout_context() -> None:
    session.pop("nexus_user", None)


def current_user_context() -> TenantUserContext:
    if not is_saas_mode():
        return TenantUserContext(
            user=User(id="local-admin", email="local@nexus.local", role=ROLE_ADMIN, tenant_id="local"),
            tenant_id="local",
            saas_mode=False,
        )

    session_user = session.get("nexus_user") or {}
    if session_user:
        role = str(session_user.get("role", "")).lower()
        if role in VALID_ROLES:
            tenant_id = str(session_user.get("tenant_id") or "default")
            return TenantUserContext(
                user=User(
                    id=str(session_user.get("id") or "session-user"),
                    email=str(session_user.get("email") or ""),
                    role=role,
                    tenant_id=tenant_id,
                ),
                tenant_id=tenant_id,
                saas_mode=True,
            )

    # Trusted reverse-proxy/dev headers remain supported for future auth providers.
    user_id = request.headers.get("X-Nexus-User-Id")
    tenant_id = request.headers.get("X-Nexus-Tenant-Id")
    role = request.headers.get("X-Nexus-Role", "").lower()
    email = request.headers.get("X-Nexus-Email", "")
    if not user_id or not tenant_id or role not in VALID_ROLES:
        return TenantUserContext(user=None, tenant_id=tenant_id or "unknown", saas_mode=True)

    return TenantUserContext(
        user=User(id=user_id, email=email, role=role, tenant_id=tenant_id),
        tenant_id=tenant_id,
        saas_mode=True,
    )


def permissions_for_role(role: str) -> list[str]:
    return sorted(ROLE_PERMISSIONS.get(str(role or "").lower(), {"read"}))


def context_permissions(context: TenantUserContext) -> list[str]:
    return permissions_for_role(context.role)


def require_role(*roles: str) -> Callable:
    """Decorator for future SaaS-only route protection.

    In local mode this is a no-op. In SaaS mode it rejects unauthenticated users
    and users without one of the allowed roles.
    """

    allowed = {role.lower() for role in roles}

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            context = current_user_context()
            if not context.saas_mode:
                return fn(*args, **kwargs)
            if not context.is_authenticated:
                return jsonify({"status": "error", "message": "Authentication required"}), 401
            if allowed and context.role not in allowed:
                return jsonify({"status": "error", "message": "Insufficient role"}), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_permission(permission: str) -> Callable:
    """Require a named permission when SaaS mode is active."""

    permission = str(permission or "").strip().lower()

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            context = current_user_context()
            if not context.saas_mode:
                return fn(*args, **kwargs)
            if not context.is_authenticated:
                return jsonify({"status": "error", "message": "Authentication required"}), 401
            if permission not in ROLE_PERMISSIONS.get(context.role, set()):
                return jsonify({"status": "error", "message": f"Missing permission: {permission}"}), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator
