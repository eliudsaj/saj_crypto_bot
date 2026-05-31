"""SQLite-backed license management.

The module is intentionally dependency-free so licensing can work before the
trading engine, MT5, or SaaS components are fully initialized.
"""

from __future__ import annotations

import hashlib
import os
import platform
import secrets
import socket
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "licenses.db"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def bool_from_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def current_machine_identity(machine_id: str | None = None) -> dict[str, str]:
    hostname = socket.gethostname() or "unknown-host"
    raw_machine = machine_id or os.getenv("NEXUS_MACHINE_ID") or f"{hostname}:{uuid.getnode()}"
    os_parts = [
        platform.system(),
        platform.release(),
        platform.version(),
        platform.machine(),
        platform.processor(),
    ]
    os_raw = "|".join(str(part or "") for part in os_parts)
    os_fingerprint = hashlib.sha256(os_raw.encode("utf-8")).hexdigest()
    return {
        "machine_id": str(raw_machine),
        "hostname": hostname,
        "os_fingerprint": os_fingerprint,
    }


def generate_license_key() -> str:
    return "NEXUS-" + "-".join(secrets.token_hex(2).upper() for _ in range(4))


@dataclass
class ValidationResult:
    valid: bool
    status: str
    reason: str
    license: dict[str, Any] | None = None
    days_remaining: int | None = None
    grace_days_remaining: int | None = None
    trading_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "status": self.status,
            "reason": self.reason,
            "license": self.license,
            "days_remaining": self.days_remaining,
            "grace_days_remaining": self.grace_days_remaining,
            "trading_allowed": self.trading_allowed,
        }


class LicenseManager:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or os.getenv("LICENSE_DB_PATH") or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def migrate(self):
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS licenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    license_key TEXT NOT NULL UNIQUE,
                    customer_name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    activated_at TEXT,
                    expires_at TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    max_accounts INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    machine_id TEXT,
                    hostname TEXT,
                    os_fingerprint TEXT,
                    machine_bound_at TEXT,
                    revoked_at TEXT,
                    last_validated_at TEXT
                )
                """
            )
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(licenses)").fetchall()}
            additions = {
                "machine_id": "TEXT",
                "hostname": "TEXT",
                "os_fingerprint": "TEXT",
                "machine_bound_at": "TEXT",
                "revoked_at": "TEXT",
                "last_validated_at": "TEXT",
            }
            for column, col_type in additions.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE licenses ADD COLUMN {column} {col_type}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_licenses_key ON licenses (license_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_licenses_email ON licenses (email)")

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["is_active"] = bool(data.get("is_active"))
        return data

    def list_licenses(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM licenses ORDER BY created_at DESC, id DESC").fetchall()
        return [self._row_to_dict(row) for row in rows if row is not None]

    def get(self, license_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM licenses WHERE license_key = ?", (str(license_key),)).fetchone()
        return self._row_to_dict(row)

    def create_license(
        self,
        customer_name: str,
        email: str,
        max_accounts: int = 1,
        license_key: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        key = (license_key or generate_license_key()).strip().upper()
        now = iso_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO licenses (
                    license_key, customer_name, email, activated_at, expires_at,
                    is_active, max_accounts, created_at
                ) VALUES (?, ?, ?, NULL, ?, 1, ?, ?)
                """,
                (key, str(customer_name or "").strip(), str(email or "").strip().lower(), expires_at, int(max_accounts or 1), now),
            )
        return self.get(key)

    def revoke(self, license_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE licenses SET is_active = 0, revoked_at = ? WHERE license_key = ?",
                (iso_now(), str(license_key).strip().upper()),
            )
        return self.get(license_key)

    def extend(self, license_key: str, days: int = 365) -> dict[str, Any] | None:
        license_row = self.get(license_key)
        if not license_row:
            return None
        current_expiry = parse_dt(license_row.get("expires_at")) or utc_now()
        base = max(current_expiry, utc_now())
        new_expiry = base + timedelta(days=max(1, int(days or 365)))
        with self.connect() as conn:
            conn.execute(
                "UPDATE licenses SET expires_at = ?, is_active = 1, revoked_at = NULL WHERE license_key = ?",
                (new_expiry.isoformat(), str(license_key).strip().upper()),
            )
        return self.get(license_key)

    def reset_machine(self, license_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE licenses
                SET machine_id = NULL, hostname = NULL, os_fingerprint = NULL, machine_bound_at = NULL
                WHERE license_key = ?
                """,
                (str(license_key).strip().upper(),),
            )
        return self.get(license_key)

    def activate(self, license_key: str, machine_id: str | None = None) -> ValidationResult:
        key = str(license_key or "").strip().upper()
        identity = current_machine_identity(machine_id)
        license_row = self.get(key)
        if not license_row:
            return ValidationResult(False, "missing", "License key not found", trading_allowed=False)
        if not license_row.get("is_active"):
            return ValidationResult(False, "revoked", "License has been revoked", license_row, trading_allowed=False)

        bound_machine = license_row.get("machine_id")
        if bound_machine and bound_machine != identity["machine_id"]:
            return ValidationResult(False, "machine_mismatch", "License is already bound to another machine", license_row, trading_allowed=False)

        now = utc_now()
        activated_at = parse_dt(license_row.get("activated_at")) or now
        expires_at = parse_dt(license_row.get("expires_at")) or (activated_at + timedelta(days=365))
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE licenses
                SET activated_at = ?, expires_at = ?, machine_id = ?, hostname = ?,
                    os_fingerprint = ?, machine_bound_at = COALESCE(machine_bound_at, ?),
                    last_validated_at = ?
                WHERE license_key = ?
                """,
                (
                    activated_at.isoformat(),
                    expires_at.isoformat(),
                    identity["machine_id"],
                    identity["hostname"],
                    identity["os_fingerprint"],
                    now.isoformat(),
                    now.isoformat(),
                    key,
                ),
            )
        return self.validate(key, machine_id=identity["machine_id"])

    def validate(self, license_key: str | None = None, machine_id: str | None = None) -> ValidationResult:
        key = str(license_key or os.getenv("LICENSE_KEY") or "").strip().upper()
        grace_days = max(0, int(os.getenv("LICENSE_GRACE_DAYS", "7") or 7))
        if bool_from_env("LICENSE_BYPASS", False):
            return ValidationResult(True, "bypassed", "License bypass enabled", trading_allowed=True)
        if not key:
            return ValidationResult(False, "missing", "No LICENSE_KEY configured", trading_allowed=False)

        identity = current_machine_identity(machine_id)
        license_row = self.get(key)
        if not license_row:
            return ValidationResult(False, "missing", "License key not found", trading_allowed=False)
        if not license_row.get("is_active"):
            return ValidationResult(False, "revoked", "License has been revoked", license_row, trading_allowed=False)
        if license_row.get("machine_id") and license_row.get("machine_id") != identity["machine_id"]:
            return ValidationResult(False, "machine_mismatch", "License is bound to another machine", license_row, trading_allowed=False)
        if license_row.get("os_fingerprint") and license_row.get("os_fingerprint") != identity["os_fingerprint"]:
            return ValidationResult(False, "os_mismatch", "OS fingerprint does not match activated machine", license_row, trading_allowed=False)

        expires_at = parse_dt(license_row.get("expires_at"))
        if not expires_at:
            return ValidationResult(False, "not_activated", "License has not been activated", license_row, trading_allowed=False)

        now = utc_now()
        days_remaining = int((expires_at - now).total_seconds() // 86400)
        grace_ends = expires_at + timedelta(days=grace_days)
        if now > grace_ends:
            return ValidationResult(False, "expired", "License expired and grace period ended", license_row, days_remaining, 0, False)
        if now > expires_at:
            grace_remaining = max(0, int((grace_ends - now).total_seconds() // 86400))
            return ValidationResult(True, "grace", "License expired but grace period is active", license_row, days_remaining, grace_remaining, True)

        with self.connect() as conn:
            conn.execute("UPDATE licenses SET last_validated_at = ? WHERE license_key = ?", (now.isoformat(), key))
        status = "expiring_soon" if days_remaining <= 30 else "active"
        return ValidationResult(True, status, "License is valid", license_row, days_remaining, grace_days, True)


_manager: LicenseManager | None = None


def get_license_manager() -> LicenseManager:
    global _manager
    if _manager is None:
        _manager = LicenseManager()
    return _manager

