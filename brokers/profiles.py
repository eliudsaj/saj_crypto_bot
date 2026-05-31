"""Broker profile persistence and adapter factory."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from .binance_adapter import BinanceBrokerAdapter
from .mt5_adapter import MT5BrokerAdapter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DEFAULT_DB_PATH = os.path.join(DATA_DIR, "brokers.db")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class BrokerProfileManager:
    def __init__(self, db_path: str | None = None):
        load_dotenv(os.path.join(BASE_DIR, ".env"))
        self.db_path = db_path or os.getenv("BROKER_DB_PATH") or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.ensure_schema()
        self.seed_default_mt5_profile()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS broker_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    broker_type TEXT NOT NULL,
                    account TEXT,
                    server TEXT,
                    password TEXT,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    is_disabled INTEGER NOT NULL DEFAULT 0,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_broker_profiles_active ON broker_profiles(is_active, is_disabled)")
            mt5_count = conn.execute(
                "SELECT COUNT(*) FROM broker_profiles WHERE broker_type = 'mt5' AND is_disabled = 0"
            ).fetchone()[0]
            active_count = conn.execute(
                "SELECT COUNT(*) FROM broker_profiles WHERE is_active = 1 AND is_disabled = 0"
            ).fetchone()[0]
            if mt5_count and active_count == 0:
                conn.execute(
                    "UPDATE broker_profiles SET is_active = 1 WHERE id = (SELECT id FROM broker_profiles WHERE broker_type = 'mt5' AND is_disabled = 0 ORDER BY id LIMIT 1)"
                )

    def seed_default_mt5_profile(self) -> None:
        with self._connect() as conn:
            existing = conn.execute("SELECT COUNT(*) FROM broker_profiles").fetchone()[0]
            if existing:
                return
            account = os.getenv("MT5_ACCOUNT", "")
            server = os.getenv("MT5_SERVER", "")
            password = os.getenv("MT5_PASSWORD", "")
            now = utc_now()
            conn.execute(
                """
                INSERT INTO broker_profiles
                    (name, broker_type, account, server, password, is_active, is_disabled, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, 0, ?, ?, ?)
                """,
                ("Default MT5", "mt5", account, server, password, json.dumps({"source": "env_seed"}), now, now),
            )

    def _row_to_dict(self, row) -> dict[str, Any]:
        data = dict(row)
        data["is_active"] = bool(data.get("is_active"))
        data["is_disabled"] = bool(data.get("is_disabled"))
        try:
            data["metadata"] = json.loads(data.get("metadata") or "{}")
        except Exception:
            data["metadata"] = {}
        data["password_configured"] = bool(data.get("password"))
        data.pop("password", None)
        return data

    def _row_to_secret_dict(self, row) -> dict[str, Any]:
        data = dict(row)
        data["is_active"] = bool(data.get("is_active"))
        data["is_disabled"] = bool(data.get("is_disabled"))
        try:
            data["metadata"] = json.loads(data.get("metadata") or "{}")
        except Exception:
            data["metadata"] = {}
        return data

    def list_profiles(self, include_disabled: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM broker_profiles"
        params = []
        if not include_disabled:
            query += " WHERE is_disabled = 0"
        query += " ORDER BY is_active DESC, id ASC"
        with self._connect() as conn:
            return [self._row_to_dict(row) for row in conn.execute(query, params).fetchall()]

    def add_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "MT5 Account").strip()
        broker_type = str(payload.get("broker_type") or "mt5").strip().lower()
        if broker_type not in {"mt5", "binance", "bybit", "ctrader"}:
            raise ValueError(f"Unsupported broker type: {broker_type}")
        now = utc_now()
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        make_active = bool(payload.get("is_active"))
        with self._connect() as conn:
            if make_active:
                conn.execute("UPDATE broker_profiles SET is_active = 0")
            cur = conn.execute(
                """
                INSERT INTO broker_profiles
                    (name, broker_type, account, server, password, is_active, is_disabled, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    name,
                    broker_type,
                    str(payload.get("account") or ""),
                    str(payload.get("server") or ""),
                    str(payload.get("password") or ""),
                    1 if make_active else 0,
                    json.dumps(metadata),
                    now,
                    now,
                ),
            )
            if not make_active and conn.execute("SELECT COUNT(*) FROM broker_profiles WHERE is_active = 1 AND is_disabled = 0").fetchone()[0] == 0:
                conn.execute("UPDATE broker_profiles SET is_active = 1 WHERE id = ?", (cur.lastrowid,))
        return self.get_profile(cur.lastrowid) or {}

    def update_profile(self, profile_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = ["name", "broker_type", "account", "server", "password"]
        updates = []
        values = []
        for key in allowed:
            if key in payload:
                value = str(payload.get(key) or "")
                if key == "password" and not value:
                    continue
                if key == "broker_type":
                    value = value.lower()
                    if value not in {"mt5", "binance", "bybit", "ctrader"}:
                        raise ValueError(f"Unsupported broker type: {value}")
                updates.append(f"{key} = ?")
                values.append(value)
        if "metadata" in payload and isinstance(payload.get("metadata"), dict):
            updates.append("metadata = ?")
            values.append(json.dumps(payload["metadata"]))
        updates.append("updated_at = ?")
        values.append(utc_now())
        values.append(profile_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE broker_profiles SET {', '.join(updates)} WHERE id = ?", values)
        if "is_active" in payload and payload.get("is_active"):
            self.set_active(profile_id)
        return self.get_profile(profile_id) or {}

    def disable_profile(self, profile_id: int, disabled: bool = True) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                "UPDATE broker_profiles SET is_disabled = ?, is_active = CASE WHEN ? = 1 THEN 0 ELSE is_active END, updated_at = ? WHERE id = ?",
                (1 if disabled else 0, 1 if disabled else 0, utc_now(), profile_id),
            )
        return self.get_profile(profile_id) or {}

    def set_active(self, profile_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM broker_profiles WHERE id = ? AND is_disabled = 0", (profile_id,)).fetchone()
            if not row:
                raise ValueError("Broker profile not found or disabled")
            conn.execute("UPDATE broker_profiles SET is_active = 0")
            conn.execute("UPDATE broker_profiles SET is_active = 1, updated_at = ? WHERE id = ?", (utc_now(), profile_id))
        return self.get_profile(profile_id) or {}

    def get_profile(self, profile_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM broker_profiles WHERE id = ?", (profile_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def get_active_profile(self, include_secret: bool = True) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM broker_profiles WHERE is_active = 1 AND is_disabled = 0 ORDER BY id LIMIT 1"
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT * FROM broker_profiles WHERE is_disabled = 0 ORDER BY id LIMIT 1"
                ).fetchone()
        if not row:
            return None
        return self._row_to_secret_dict(row) if include_secret else self._row_to_dict(row)

    def create_adapter(self, profile: dict[str, Any] | None = None):
        profile = profile or self.get_active_profile(include_secret=True) or {}
        broker_type = str(profile.get("broker_type") or "mt5").lower()
        if broker_type == "mt5":
            return MT5BrokerAdapter(profile)
        if broker_type == "binance":
            return BinanceBrokerAdapter(profile)
        raise NotImplementedError(f"{broker_type} adapter is planned but not implemented yet")

    def test_connection(self, profile_id: int) -> dict[str, Any]:
        profile = self.get_active_profile(include_secret=True) if not profile_id else None
        if profile_id:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM broker_profiles WHERE id = ?", (profile_id,)).fetchone()
            profile = self._row_to_secret_dict(row) if row else None
        if not profile:
            return {"connected": False, "message": "Broker profile not found"}
        try:
            adapter = self.create_adapter(profile)
            connected = adapter.connect()
            account = adapter.get_account_info() if connected else None
            adapter.disconnect()
        except Exception as exc:
            return {
                "connected": False,
                "message": str(exc),
                "account": None,
                "profile": self._row_to_dict(profile) if isinstance(profile.get("metadata"), str) else {k: v for k, v in profile.items() if k != "password"},
            }
        return {
            "connected": connected,
            "message": "Connection successful" if connected else (getattr(adapter, "last_order_error", None) or "Connection failed"),
            "account": account,
            "profile": self._row_to_dict(profile) if isinstance(profile.get("metadata"), str) else {k: v for k, v in profile.items() if k != "password"},
        }


_manager: BrokerProfileManager | None = None


def get_broker_manager() -> BrokerProfileManager:
    global _manager
    if _manager is None:
        _manager = BrokerProfileManager()
    return _manager
