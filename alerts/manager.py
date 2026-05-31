"""Persistent alert manager with optional realtime push."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from typing import Callable


SEVERITIES = {"info", "warning", "danger", "success"}
CATEGORIES = {"system", "risk", "trade", "execution", "config"}


class AlertManager:
    def __init__(self, path: str = "data/alerts.jsonl"):
        self.path = path
        self._lock = threading.RLock()
        self._emitter: Callable[[dict], None] | None = None
        self._last_by_key: dict[str, datetime] = {}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def set_emitter(self, emitter: Callable[[dict], None] | None):
        """Register an optional realtime emitter. File persistence still works without it."""
        with self._lock:
            self._emitter = emitter

    def create(
        self,
        title: str,
        message: str,
        severity: str = "info",
        category: str = "system",
        symbol: str | None = None,
        event: str | None = None,
        metadata: dict | None = None,
        dedupe_key: str | None = None,
        cooldown_seconds: int = 0,
    ) -> dict | None:
        severity = severity if severity in SEVERITIES else "info"
        category = category if category in CATEGORIES else "system"

        with self._lock:
            now = datetime.now()
            if dedupe_key and cooldown_seconds > 0:
                last_seen = self._last_by_key.get(dedupe_key)
                if last_seen and now - last_seen < timedelta(seconds=cooldown_seconds):
                    return None
                self._last_by_key[dedupe_key] = now

            alert = {
                "id": uuid.uuid4().hex,
                "timestamp": now.isoformat(),
                "title": str(title),
                "message": str(message),
                "severity": severity,
                "category": category,
                "symbol": symbol,
                "event": event,
                "metadata": metadata or {},
                "read": False,
            }

            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(alert, separators=(",", ":")) + "\n")

            emitter = self._emitter

        if emitter:
            try:
                emitter(alert)
            except Exception:
                pass
        return alert

    def list(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit or 100), 1000))
        if not os.path.exists(self.path):
            return []

        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    lines = handle.readlines()
            except FileNotFoundError:
                return []

        alerts: list[dict] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                loaded = json.loads(line)
                if isinstance(loaded, dict):
                    alerts.append(loaded)
            except json.JSONDecodeError:
                continue
        return alerts

    def clear(self) -> int:
        with self._lock:
            existing = self.list(1000)
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8"):
                pass
            self._last_by_key.clear()
            return len(existing)


alert_manager = AlertManager()
