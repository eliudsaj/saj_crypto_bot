"""SQLite-backed users for SaaS role-based access control."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .models import ROLE_ADMIN, VALID_ROLES, User

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DEFAULT_DB_PATH = os.path.join(DATA_DIR, "users.db")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, salt, expected = str(stored_hash or "").split("$", 2)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    actual = _hash_password(password, salt).split("$", 2)[2]
    return hmac.compare_digest(actual, expected)


class UserStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.getenv("USERS_DB_PATH") or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.ensure_schema()
        self.seed_bootstrap_admin()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def seed_bootstrap_admin(self) -> None:
        email = os.getenv("SAAS_ADMIN_EMAIL", "admin@nexus.local").strip().lower()
        password = os.getenv("SAAS_ADMIN_PASSWORD", "change-me-now")
        tenant_id = os.getenv("SAAS_DEFAULT_TENANT", "default").strip() or "default"
        if not email or not password:
            return
        with self._connect() as conn:
            existing = conn.execute("SELECT id FROM users WHERE lower(email) = ?", (email,)).fetchone()
            if existing:
                return
            now = utc_now()
            conn.execute(
                """
                INSERT INTO users (email, password_hash, role, tenant_id, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (email, _hash_password(password), ROLE_ADMIN, tenant_id, now, now),
            )

    def _row_to_user(self, row) -> User | None:
        if not row:
            return None
        return User(
            id=str(row["id"]),
            email=row["email"],
            role=row["role"],
            tenant_id=row["tenant_id"],
            is_active=bool(row["is_active"]),
        )

    def _row_to_public(self, row) -> dict[str, Any]:
        user = self._row_to_user(row)
        if not user:
            return {}
        data = user.to_public_dict()
        data["created_at"] = row["created_at"]
        data["updated_at"] = row["updated_at"]
        return data

    def authenticate(self, email: str, password: str) -> User | None:
        email = str(email or "").strip().lower()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()
        if not row or not bool(row["is_active"]):
            return None
        if not _verify_password(password, row["password_hash"]):
            return None
        return self._row_to_user(row)

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY role ASC, email ASC").fetchall()
        return [self._row_to_public(row) for row in rows]

    def create_user(self, email: str, password: str, role: str, tenant_id: str | None = None) -> dict[str, Any]:
        email = str(email or "").strip().lower()
        role = str(role or "").strip().lower()
        tenant_id = tenant_id or os.getenv("SAAS_DEFAULT_TENANT", "default").strip() or "default"
        if not email or not password:
            raise ValueError("Email and password are required")
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role: {role}")
        now = utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO users (email, password_hash, role, tenant_id, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (email, _hash_password(password), role, tenant_id, now, now),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        return self._row_to_public(row)

    def update_user(self, user_id: int, role: str | None = None, is_active: bool | None = None, password: str | None = None) -> dict[str, Any]:
        updates = []
        values = []
        if role is not None:
            role = str(role).strip().lower()
            if role not in VALID_ROLES:
                raise ValueError(f"Invalid role: {role}")
            updates.append("role = ?")
            values.append(role)
        if is_active is not None:
            updates.append("is_active = ?")
            values.append(1 if is_active else 0)
        if password:
            updates.append("password_hash = ?")
            values.append(_hash_password(password))
        if not updates:
            return self.get_user(user_id) or {}
        updates.append("updated_at = ?")
        values.append(utc_now())
        values.append(user_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_public(row)

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_public(row) if row else None


_store: UserStore | None = None


def get_user_store() -> UserStore:
    global _store
    if _store is None:
        _store = UserStore()
    return _store
