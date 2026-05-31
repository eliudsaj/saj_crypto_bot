"""JSONL strategy journal for scanned setup decisions."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime


class StrategyJournalWriter:
    def __init__(self, path: str = "data/strategy_journal.jsonl"):
        self.path = path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def write(self, entry: dict) -> dict:
        payload = {
            "timestamp": datetime.now().isoformat(),
            **(entry or {}),
        }
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, default=str, separators=(",", ":")) + "\n")
        return payload

    def read(self, limit: int = 250, filters: dict | None = None) -> list[dict]:
        filters = filters or {}
        if not os.path.exists(self.path):
            return []

        rows = []
        with self._lock:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if self._matches(item, filters):
                        rows.append(item)

        return rows[-max(1, int(limit or 250)):][::-1]

    def _matches(self, item: dict, filters: dict) -> bool:
        for key in ["symbol", "grade", "trade_type"]:
            expected = str(filters.get(key) or "").strip().upper()
            if expected and str(item.get(key) or "").strip().upper() != expected:
                return False
        decision = str(filters.get("decision") or "").strip().upper()
        if decision and str(item.get("execution_decision") or "").strip().upper() != decision:
            return False
        date_filter = str(filters.get("date") or "").strip()
        if date_filter and not str(item.get("timestamp") or "").startswith(date_filter):
            return False
        return True


strategy_journal = StrategyJournalWriter()
