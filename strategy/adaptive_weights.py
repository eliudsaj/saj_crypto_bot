"""Interpretable adaptive weighting for strategy conditions.

This module adjusts live setup scores from realized outcomes. It deliberately
uses simple statistics, sample thresholds, capped multipliers, and decay instead
of machine-learning libraries so every adjustment can be explained in the UI.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from analytics.edge_diagnostics import build_edge_diagnostics


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
STATE_PATH = DATA_DIR / "adaptive_weights.json"


@dataclass
class AdaptiveConfig:
    enabled: bool = True
    min_sample: int = 12
    min_confidence: float = 0.55
    refresh_seconds: int = 300
    lookback_limit: int = 20
    max_boost: float = 0.12
    max_penalty: float = 0.22
    severe_drawdown_r: float = -6.0
    cooldown_minutes: int = 90
    toxic_expectancy: float = -0.15
    toxic_profit_factor: float = 0.65

    @classmethod
    def from_env(cls) -> "AdaptiveConfig":
        def as_bool(key: str, default: bool) -> bool:
            return str(os.getenv(key, str(default))).strip().lower() in {"1", "true", "yes", "on"}

        def as_int(key: str, default: int) -> int:
            try:
                return int(float(os.getenv(key, default)))
            except (TypeError, ValueError):
                return default

        def as_float(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, default))
            except (TypeError, ValueError):
                return default

        return cls(
            enabled=as_bool("ADAPTIVE_WEIGHTS_ENABLED", True),
            min_sample=max(3, as_int("ADAPTIVE_MIN_SAMPLE", 12)),
            min_confidence=max(0.0, min(1.0, as_float("ADAPTIVE_MIN_CONFIDENCE", 0.55))),
            refresh_seconds=max(30, as_int("ADAPTIVE_REFRESH_SECONDS", 300)),
            max_boost=max(0.0, min(0.30, as_float("ADAPTIVE_MAX_BOOST", 0.12))),
            max_penalty=max(0.0, min(0.50, as_float("ADAPTIVE_MAX_PENALTY", 0.22))),
            severe_drawdown_r=as_float("ADAPTIVE_SEVERE_DRAWDOWN_R", -6.0),
            cooldown_minutes=max(5, as_int("ADAPTIVE_COOLDOWN_MINUTES", 90)),
            toxic_expectancy=as_float("ADAPTIVE_TOXIC_EXPECTANCY_R", -0.15),
            toxic_profit_factor=as_float("ADAPTIVE_TOXIC_PROFIT_FACTOR", 0.65),
        )


class AdaptiveWeightManager:
    def __init__(self, config: AdaptiveConfig | None = None, state_path: Path = STATE_PATH):
        self.config = config or AdaptiveConfig.from_env()
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_refresh: datetime | None = None
        self._diagnostics: dict[str, Any] = {}
        self._index: dict[tuple[str, str], dict[str, Any]] = {}
        self._rolling: dict[str, float] = {}
        self._state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"cooldown_until": None, "toxic_symbols": []}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"cooldown_until": None, "toxic_symbols": []}
        except Exception:
            return {"cooldown_until": None, "toxic_symbols": []}

    def _save_state(self) -> None:
        payload = {
            **self._state,
            "updated_at": datetime.now().isoformat(),
        }
        self.state_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def refresh(self, force: bool = False) -> dict[str, Any]:
        now = datetime.now()
        if (
            not force
            and self._last_refresh
            and (now - self._last_refresh).total_seconds() < self.config.refresh_seconds
            and self._diagnostics
        ):
            return self._diagnostics

        diagnostics = build_edge_diagnostics(min_sample=max(3, self.config.min_sample), limit=50)
        self._diagnostics = diagnostics
        self._index = {}
        for component, rows in (diagnostics.get("components") or {}).items():
            for row in rows:
                value = str(row.get("value") or "unknown")
                self._index[(component, value)] = row

        self._rolling = self._rolling_metrics(limit=self.config.lookback_limit)
        self._update_toxic_state(diagnostics, self._rolling)
        self._last_refresh = now
        return diagnostics

    def _update_toxic_state(self, diagnostics: dict[str, Any], rolling: dict[str, float]) -> None:
        toxic_symbols = []
        for row in (diagnostics.get("components") or {}).get("symbol", []):
            ci = row.get("confidence_interval") or {}
            ci_high = ci.get("high")
            if (
                row.get("sample_size", 0) >= self.config.min_sample
                and (row.get("average_r") is not None and row.get("average_r") <= self.config.toxic_expectancy)
                and row.get("profit_factor", 0) <= self.config.toxic_profit_factor
                and (ci_high is None or ci_high < 0)
            ):
                toxic_symbols.append(str(row.get("value") or "").upper())

        if rolling["max_drawdown_r"] <= self.config.severe_drawdown_r:
            cooldown_until = datetime.now() + timedelta(minutes=self.config.cooldown_minutes)
            self._state["cooldown_until"] = cooldown_until.isoformat()

        self._state["toxic_symbols"] = sorted(set(toxic_symbols))
        self._save_state()

    def _recent_r_values(self, limit: int = 20) -> list[float]:
        return [item["r"] for item in self._recent_trade_values(limit=limit) if item.get("r") is not None]

    def _recent_trade_values(self, limit: int = 20) -> list[dict[str, float]]:
        rows: list[tuple[datetime, dict[str, float]]] = []
        log_dir = BASE_DIR / "logs"
        if not log_dir.exists():
            return []
        for path in sorted(log_dir.glob("trades_*.json"), reverse=True)[:10]:
            if ".corrupt-" in path.name:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, list):
                continue
            for entry in reversed(data):
                if not isinstance(entry, dict) or entry.get("event") != "TRADE_CLOSED":
                    continue
                profit = self._as_float(entry.get("profit"), 0.0)
                if abs(profit) <= 1e-9:
                    continue
                risk = self._as_float(entry.get("risk"), 0.0)
                raw_r = self._as_float(entry.get("r"), None)
                r_value = raw_r if raw_r is not None else (profit / risk if risk > 0 else None)
                timestamp = self._parse_time(entry.get("timestamp"))
                if timestamp:
                    payload = {"profit": float(profit)}
                    if r_value is not None and math.isfinite(r_value):
                        payload["r"] = float(r_value)
                    rows.append((timestamp, payload))
            if len(rows) >= limit:
                break
        rows.sort(key=lambda item: item[0])
        return [value for _, value in rows[-max(1, limit):]]

    def _rolling_metrics(self, limit: int = 20) -> dict[str, float]:
        rows = self._recent_trade_values(limit=limit)
        profits = [row["profit"] for row in rows]
        r_values = [row["r"] for row in rows if row.get("r") is not None]
        wins = [value for value in profits if value > 0]
        losses = [value for value in profits if value < 0]
        mean = sum(profits) / len(profits) if profits else 0.0
        if len(profits) > 1:
            import statistics
            sd = statistics.stdev(profits)
            sharpe = mean / sd * math.sqrt(len(profits)) if sd > 0 else 0.0
        else:
            sharpe = 0.0
        return {
            "sample_size": float(len(profits)),
            "expectancy": mean,
            "average_r": sum(r_values) / len(r_values) if r_values else 0.0,
            "win_rate": len(wins) / len(profits) if profits else 0.0,
            "sharpe": sharpe,
            "profit_factor": sum(wins) / abs(sum(losses)) if losses else (None if wins else 0.0),
            "max_drawdown_r": self._max_drawdown(r_values) if r_values else 0.0,
        }

    @staticmethod
    def _max_drawdown(values: list[float]) -> float:
        equity = 0.0
        peak = 0.0
        worst = 0.0
        for value in values:
            equity += value
            peak = max(peak, equity)
            worst = min(worst, equity - peak)
        return worst

    @staticmethod
    def _as_float(value: Any, default: float | None = 0.0) -> float | None:
        try:
            parsed = float(value)
            return parsed if math.isfinite(parsed) else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _cooldown_active(self) -> tuple[bool, str | None]:
        raw = self._state.get("cooldown_until")
        if not raw:
            return False, None
        try:
            until = datetime.fromisoformat(str(raw))
        except ValueError:
            return False, None
        if datetime.now() >= until:
            self._state["cooldown_until"] = None
            self._save_state()
            return False, None
        return True, f"Adaptive cooldown active until {until.strftime('%H:%M')}"

    def _component_values(self, signal: dict) -> dict[str, str]:
        setup = signal.get("setup_score") or {}
        components = setup.get("components") or []
        passed_keys = {str(c.get("key") or "").lower() for c in components if c.get("passed")}
        missing_keys = {str(c.get("key") or "").lower() for c in components if not c.get("passed")}
        session = signal.get("session_bias") or setup.get("session_bias") or {}
        spread = signal.get("spread_safety") or setup.get("spread") or {}
        displacement = signal.get("displacement") or setup.get("displacement") or {}

        def presence(*keys: str) -> str:
            lowered = {key.lower() for key in keys}
            if passed_keys.intersection(lowered):
                return "present"
            if missing_keys.intersection(lowered):
                return "missing"
            return "unknown"

        spread_state = "safe" if spread.get("safe") is True else "unsafe" if spread.get("safe") is False else "unknown"
        volatility = signal.get("market_volatility") or signal.get("volatility_state") or displacement.get("quality") or "unknown"
        return {
            "symbol": str(signal.get("symbol") or "unknown").upper(),
            "session": str(session.get("label") or session.get("description") or "unknown"),
            "archetype": str(setup.get("archetype") or "unknown"),
            "displacement_quality": presence("displacement"),
            "htf_alignment": presence("htf_bias", "htf"),
            "liquidity_sweeps": presence("liquidity_sweep"),
            "fvg_quality": presence("ob_fvg", "fvg"),
            "spread_safety": spread_state,
            "volatility_conditions": str(volatility),
        }

    def _lookup_metric(self, component: str, value: str) -> dict[str, Any] | None:
        aliases = {
            "liquidity_sweeps": "liquidity_sweep_presence",
            "fvg_quality": "fvg_presence",
            "spread_safety": "spread_state",
            "volatility_conditions": "market_volatility",
        }
        return self._index.get((aliases.get(component, component), value))

    def _condition_adjustment(self, metric: dict[str, Any]) -> tuple[float, str]:
        sample = int(metric.get("sample_size") or 0)
        if sample < self.config.min_sample:
            return 0.0, "insufficient sample"

        expectancy = float(metric.get("expectancy") or 0.0)
        avg_r = metric.get("average_r")
        avg_r = float(avg_r) if avg_r is not None and math.isfinite(float(avg_r)) else None
        profit_factor = float(metric.get("profit_factor") or 0.0)
        ci = metric.get("confidence_interval") or {}
        ci_low = ci.get("low")
        ci_high = ci.get("high")

        positive = (avg_r is not None and avg_r > 0.10) or (expectancy > 0 and profit_factor > 1.15)
        negative = (avg_r is not None and avg_r < -0.10) or (expectancy < 0 and profit_factor < 0.85)

        if positive and (ci_low is None or ci_low >= 0):
            strength = min(self.config.max_boost, 0.03 + min(0.09, abs(avg_r or expectancy) * 0.08))
            return strength, "profitable condition"
        if negative and (ci_high is None or ci_high <= 0):
            strength = -min(self.config.max_penalty, 0.04 + min(0.18, abs(avg_r or expectancy) * 0.10))
            return strength, "weak condition"
        if negative:
            return -min(self.config.max_penalty * 0.5, 0.03 + min(0.08, abs(avg_r or expectancy) * 0.05)), "soft penalty"
        return 0.0, "neutral"

    def evaluate_signal(self, signal: dict) -> dict[str, Any]:
        base_score = float((signal.get("setup_score") or {}).get("score") or signal.get("confluence_score") or 0.0)
        if not self.config.enabled:
            return {
                "enabled": False,
                "base_score": base_score,
                "adjusted_score": base_score,
                "multiplier": 1.0,
                "suppressed": False,
                "confidence": 0.0,
                "explanations": ["Adaptive weighting disabled"],
            }

        diagnostics = self.refresh()
        values = self._component_values(signal)
        active_cooldown, cooldown_reason = self._cooldown_active()
        toxic_symbols = set(self._state.get("toxic_symbols") or [])
        explanations = []
        adjustments = []
        confidence_scores = []

        for component, value in values.items():
            if str(value).strip().lower() in {"", "unknown", "none", "null"}:
                continue
            metric = self._lookup_metric(component, value)
            if not metric:
                continue
            sample = int(metric.get("sample_size") or 0)
            confidence = min(1.0, sample / max(self.config.min_sample * 3, 1))
            confidence_scores.append(confidence)
            delta, reason = self._condition_adjustment(metric)
            if delta:
                adjustments.append(delta)
                explanations.append(
                    f"{component}={value}: {reason} "
                    f"(n={sample}, exp={float(metric.get('expectancy') or 0):.2f}, "
                    f"R={metric.get('average_r') if metric.get('average_r') is not None else 'n/a'})"
                )

        confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.0
        raw_delta = sum(adjustments)
        capped_delta = max(-self.config.max_penalty, min(self.config.max_boost, raw_delta))
        if capped_delta > 0 and confidence < self.config.min_confidence:
            explanations.append(f"Positive boost withheld: confidence {confidence:.2f} < {self.config.min_confidence:.2f}")
            capped_delta = 0.0

        multiplier = max(0.40, min(1.30, 1.0 + capped_delta))
        adjusted_score = max(0.0, min(1.0, base_score * multiplier))
        suppressed = False
        suppression_reason = None

        symbol = values["symbol"]
        if active_cooldown:
            suppressed = True
            suppression_reason = cooldown_reason
        elif symbol in toxic_symbols:
            suppressed = True
            suppression_reason = f"{symbol} is automatically suppressed as a toxic symbol"

        if suppression_reason:
            explanations.insert(0, suppression_reason)
        if not explanations:
            explanations.append("No statistically confident adaptive adjustment yet")

        rolling = self._rolling or self._rolling_metrics(limit=self.config.lookback_limit)

        return {
            "enabled": True,
            "base_score": round(base_score, 4),
            "adjusted_score": round(adjusted_score, 4),
            "multiplier": round(multiplier, 4),
            "delta": round(adjusted_score - base_score, 4),
            "confidence": round(confidence, 4),
            "suppressed": suppressed,
            "suppression_reason": suppression_reason,
            "toxic_symbols": sorted(toxic_symbols),
            "components": values,
            "explanations": explanations[:8],
            "rolling": {
                "expectancy": rolling["expectancy"],
                "average_r": rolling["average_r"],
                "win_rate": rolling["win_rate"],
                "sharpe": rolling["sharpe"],
                "sample_size": rolling["sample_size"],
                "max_drawdown_r": rolling["max_drawdown_r"],
            },
        }
