"""Market regime detection and regime-specific trading policy.

The detector is intentionally transparent: it classifies recent candles into a
small set of regimes using directional efficiency, EMA separation, volatility
expansion/compression, candle body quality, spread state, and event-spike
signals. Policies are conservative overlays consumed by the execution gates and
dashboard; they do not duplicate entry rules.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = BASE_DIR / "logs"


REGIME_POLICIES = {
    "trending": {
        "score_threshold_delta": 0.00,
        "strictness": "normal",
        "risk_multiplier": 1.0,
        "trailing_stop_trigger_delta": 0.00,
        "trailing_tp_extension_multiplier": 1.15,
        "management": "Let winners breathe; trailing TP can extend more.",
    },
    "ranging": {
        "score_threshold_delta": 0.12,
        "strictness": "high",
        "risk_multiplier": 0.55,
        "trailing_stop_trigger_delta": -0.05,
        "trailing_tp_extension_multiplier": 0.65,
        "management": "Require stronger confirmation; take profits faster.",
    },
    "volatile": {
        "score_threshold_delta": 0.10,
        "strictness": "high",
        "risk_multiplier": 0.50,
        "trailing_stop_trigger_delta": 0.08,
        "trailing_tp_extension_multiplier": 0.75,
        "management": "Reduce size; avoid tight trailing on noisy spikes.",
    },
    "news-driven": {
        "score_threshold_delta": 0.16,
        "strictness": "very_high",
        "risk_multiplier": 0.35,
        "trailing_stop_trigger_delta": 0.10,
        "trailing_tp_extension_multiplier": 0.60,
        "management": "Trade only confirmed retests; reduced risk.",
    },
    "low-liquidity": {
        "score_threshold_delta": 0.14,
        "strictness": "very_high",
        "risk_multiplier": 0.40,
        "trailing_stop_trigger_delta": -0.03,
        "trailing_tp_extension_multiplier": 0.55,
        "management": "Avoid unless structure is exceptional.",
    },
    "expansion": {
        "score_threshold_delta": 0.04,
        "strictness": "medium",
        "risk_multiplier": 0.85,
        "trailing_stop_trigger_delta": 0.04,
        "trailing_tp_extension_multiplier": 1.05,
        "management": "Momentum allowed with clean close quality.",
    },
    "compression": {
        "score_threshold_delta": 0.10,
        "strictness": "high",
        "risk_multiplier": 0.60,
        "trailing_stop_trigger_delta": -0.05,
        "trailing_tp_extension_multiplier": 0.70,
        "management": "Wait for expansion before committing.",
    },
    "unknown": {
        "score_threshold_delta": 0.08,
        "strictness": "high",
        "risk_multiplier": 0.70,
        "trailing_stop_trigger_delta": 0.00,
        "trailing_tp_extension_multiplier": 0.75,
        "management": "Fallback conservative behavior.",
    },
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _metrics(values: list[float], r_values: list[float]) -> dict[str, Any]:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    mean = sum(values) / len(values) if values else 0.0
    if len(values) > 1:
        import statistics

        sd = statistics.stdev(values)
        sharpe = mean / sd * math.sqrt(len(values)) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    return {
        "sample_size": len(values),
        "win_rate": len(wins) / len(values) if values else 0.0,
        "expectancy": mean,
        "average_r": sum(r_values) / len(r_values) if r_values else 0.0,
        "sharpe": sharpe,
        "max_drawdown": _max_drawdown(values),
    }


def regime_policy(regime: str | dict | None) -> dict[str, Any]:
    label = regime.get("label") if isinstance(regime, dict) else regime
    key = str(label or "unknown").strip().lower()
    return {"regime": key, **REGIME_POLICIES.get(key, REGIME_POLICIES["unknown"])}


def classify_market_regime(symbol: str, rates_df=None, spread: dict | None = None, news_move: dict | None = None) -> dict[str, Any]:
    if rates_df is None or len(rates_df) < 30:
        return {
            "symbol": symbol,
            "label": "unknown",
            "confidence": 0.0,
            "score": 0.0,
            "drivers": ["Not enough candle history"],
            "policy": regime_policy("unknown"),
        }

    try:
        ordered = rates_df.sort_values("time").reset_index(drop=True)
        close = ordered["close"].astype(float)
        high = ordered["high"].astype(float)
        low = ordered["low"].astype(float)
        open_price = ordered["open"].astype(float)
        latest_range = max(float(high.iloc[-1] - low.iloc[-1]), 1e-12)
        latest_body = abs(float(close.iloc[-1] - open_price.iloc[-1]))
        ranges = (high - low).tail(24)
        avg_range = max(float(ranges.mean()), 1e-12)
        long_avg_range = max(float((high - low).tail(50).mean()), avg_range)
        recent_range = max(float(high.tail(24).max() - low.tail(24).min()), 1e-12)
        directional_move = abs(float(close.iloc[-1] - close.iloc[-12]))
        directional_efficiency = min(1.0, directional_move / recent_range)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema36 = close.ewm(span=36, adjust=False).mean()
        ema_gap = abs(float(ema12.iloc[-1] - ema36.iloc[-1]))
        trend_score = min(1.0, ema_gap / avg_range) * 0.45 + directional_efficiency * 0.55
        volatility_ratio = latest_range / avg_range
        range_expansion = avg_range / long_avg_range if long_avg_range > 0 else 1.0
        body_to_range = latest_body / latest_range
        wick_ratio = 1.0 - body_to_range
        spread_safe = (spread or {}).get("safe") is not False
        spread_pips = _safe_float((spread or {}).get("spread_pips"), 0.0)
        max_spread = _safe_float((spread or {}).get("max_spread_pips"), 0.0)
        spread_ratio = spread_pips / max_spread if max_spread > 0 else 0.0
        news_mode = str((news_move or {}).get("mode") or "NORMAL").upper()

        drivers = []
        if news_mode in {"ACTIVE", "FOLLOW_RETEST", "WATCH"}:
            label = "news-driven"
            confidence = 0.85 if news_mode == "ACTIVE" else 0.68
            drivers.append(f"News/event mode {news_mode}")
        elif not spread_safe or spread_ratio >= 0.85:
            label = "low-liquidity"
            confidence = min(0.9, 0.55 + spread_ratio * 0.35)
            drivers.append("Spread is near or beyond acceptable limit")
        elif volatility_ratio >= 2.1 or range_expansion >= 1.55:
            label = "volatile"
            confidence = min(0.95, max(volatility_ratio / 3.0, range_expansion / 2.0))
            drivers.append("Large range expansion or volatility spike")
        elif volatility_ratio >= 1.35 and body_to_range >= 0.55 and trend_score >= 0.35:
            label = "expansion"
            confidence = min(0.9, (volatility_ratio / 2.0 + body_to_range + trend_score) / 3)
            drivers.append("Directional body expansion")
        elif range_expansion <= 0.72 or volatility_ratio <= 0.65:
            label = "compression"
            confidence = min(0.85, 1.0 - min(range_expansion, volatility_ratio))
            drivers.append("Compressed candle ranges")
        elif trend_score >= 0.48 and directional_efficiency >= 0.30:
            label = "trending"
            confidence = min(0.9, trend_score)
            drivers.append("EMA separation and directional efficiency")
        elif directional_efficiency < 0.22 or wick_ratio >= 0.68:
            label = "ranging"
            confidence = min(0.85, 0.45 + (1.0 - directional_efficiency) * 0.35)
            drivers.append("Low directional efficiency or wick-heavy price action")
        else:
            label = "ranging"
            confidence = 0.45
            drivers.append("No clean trend or expansion edge")

        payload = {
            "symbol": symbol,
            "label": label,
            "confidence": round(float(confidence), 3),
            "score": round(float(trend_score), 3),
            "trend_score": round(float(trend_score), 3),
            "directional_efficiency": round(float(directional_efficiency), 3),
            "volatility_ratio": round(float(volatility_ratio), 2),
            "range_expansion": round(float(range_expansion), 2),
            "body_to_range": round(float(body_to_range), 3),
            "spread_ratio": round(float(spread_ratio), 3),
            "drivers": drivers,
        }
        payload["policy"] = regime_policy(label)
        return payload
    except Exception as exc:
        return {
            "symbol": symbol,
            "label": "unknown",
            "confidence": 0.0,
            "score": 0.0,
            "drivers": [f"Regime detection failed: {exc}"],
            "policy": regime_policy("unknown"),
        }


def regime_performance(log_dir: Path = LOG_DIR) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"profit": [], "r": []})
    if not log_dir.exists():
        return {}
    for path in sorted(log_dir.glob("trades_*.json")):
        if ".corrupt-" in path.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict) or item.get("event") != "TRADE_CLOSED":
                continue
            profit = _safe_float(item.get("profit"), 0.0)
            if abs(profit) <= 1e-9:
                continue
            regime = item.get("market_regime") or {}
            label = regime.get("label") if isinstance(regime, dict) else regime
            label = str(label or "unknown").lower()
            buckets[label]["profit"].append(profit)
            r_value = item.get("r")
            if r_value is not None:
                buckets[label]["r"].append(_safe_float(r_value, 0.0))
    return {
        label: _metrics(values["profit"], values["r"])
        for label, values in buckets.items()
    }

