"""Forward-test validation for strategy edge.

This module separates live forward-test outcomes from historical/backtest data
and evaluates only comparable samples: same strategy version, config version,
and symbol group unless filters say otherwise.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
FORWARD_PATH = DATA_DIR / "forward_trades.jsonl"
HISTORICAL_TRADES_PATH = DATA_DIR / "trades.csv"

VALIDATION_STATUS = ("UNPROVEN", "PROMISING", "VALIDATED", "DEGRADED")


def current_strategy_context(symbols: list[str] | None = None) -> dict[str, str]:
    symbols = symbols or [s.strip().upper() for s in os.getenv("TRADING_SYMBOLS", "").split(",") if s.strip()]
    symbol_group = os.getenv("SYMBOL_GROUP", "").strip()
    if not symbol_group:
        symbol_group = _classify_symbol_group(symbols)
    config_version = os.getenv("CONFIG_VERSION", "").strip()
    if not config_version:
        config_version = _config_fingerprint()
    return {
        "strategy_version": os.getenv("STRATEGY_VERSION", "local-dev").strip() or "local-dev",
        "config_version": config_version,
        "symbol_group": symbol_group,
    }


def record_forward_trade(entry: dict[str, Any], path: Path = FORWARD_PATH) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now().isoformat(),
        "dataset": "forward",
        **current_strategy_context([entry.get("symbol")] if entry.get("symbol") else None),
        **(entry or {}),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":"), default=str) + "\n")
    return payload


def build_validation_report(
    strategy_version: str | None = None,
    config_version: str | None = None,
    symbol_group: str | None = None,
    min_sample: int | None = None,
    rolling_window: int | None = None,
) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    context = current_strategy_context()
    strategy_version = strategy_version or context["strategy_version"]
    config_version = config_version or context["config_version"]
    symbol_group = symbol_group or context["symbol_group"]
    min_sample = max(5, int(min_sample or os.getenv("VALIDATION_MIN_SAMPLE", 30)))
    rolling_window = max(5, int(rolling_window or os.getenv("VALIDATION_ROLLING_WINDOW", 30)))

    historical = _read_historical_trades()
    forward_all = _read_forward_trades()
    forward = [
        row for row in forward_all
        if str(row.get("strategy_version") or "") == strategy_version
        and str(row.get("config_version") or "") == config_version
        and str(row.get("symbol_group") or "") == symbol_group
    ]
    forward.sort(key=lambda row: str(row.get("timestamp") or ""))

    overall = _metrics(forward)
    rolling = _rolling_metrics(forward, rolling_window)
    ci = overall.get("confidence_interval") or {"low": None, "high": None}
    stable_expectancy = _stable_expectancy(rolling)
    stable_ci = ci.get("low") is not None and ci.get("low") > 0
    degraded = _detect_degradation(rolling, overall)
    status = _validation_status(
        sample_size=overall["sample_size"],
        min_sample=min_sample,
        expectancy=overall["expectancy"],
        ci=ci,
        stable_expectancy=stable_expectancy,
        stable_ci=stable_ci,
        degraded=degraded,
    )

    return {
        "status": status,
        "context": {
            "strategy_version": strategy_version,
            "config_version": config_version,
            "symbol_group": symbol_group,
        },
        "thresholds": {
            "min_sample": min_sample,
            "rolling_window": rolling_window,
            "validated_requires_ci_above_zero": True,
        },
        "datasets": {
            "historical_path": str(HISTORICAL_TRADES_PATH),
            "forward_path": str(FORWARD_PATH),
            "historical_trades": len(historical),
            "forward_trades_all": len(forward_all),
            "forward_trades_matching_context": len(forward),
        },
        "overall": overall,
        "rolling": rolling[-12:],
        "rolling_latest": rolling[-1] if rolling else None,
        "gates": {
            "minimum_sample_met": overall["sample_size"] >= min_sample,
            "expectancy_positive": overall["expectancy"] > 0,
            "confidence_interval_positive": stable_ci,
            "expectancy_stable": stable_expectancy,
            "degradation_detected": degraded,
        },
        "breakdowns": {
            "by_symbol": _group_metrics(forward, "symbol"),
            "by_strategy_version": _group_metrics(forward_all, "strategy_version"),
            "by_config_version": _group_metrics(forward_all, "config_version"),
            "by_symbol_group": _group_metrics(forward_all, "symbol_group"),
        },
    }


def _classify_symbol_group(symbols: list[str]) -> str:
    symbols = [s.upper() for s in symbols if s]
    if not symbols:
        return "default"
    if all("XAU" in s or "GOLD" in s for s in symbols):
        return "metals"
    if any("XAU" in s or "GOLD" in s for s in symbols):
        return "mixed"
    if all(s.endswith("JPY") for s in symbols):
        return "jpy"
    if len(symbols) <= 3:
        return "focused-fx"
    return "major-fx"


def _config_fingerprint() -> str:
    keys = [
        "TRADING_SYMBOLS", "RISK_PERCENT", "MAX_EXPOSURE_PERCENT",
        "EXECUTION_CONVICTION_THRESHOLD", "EXECUTION_SETUP_SCORE_THRESHOLD",
        "MIN_PROFESSIONAL_SETUP_SCORE", "FEATURE_STRICT_QUALITY_GATE",
        "MIN_STRUCTURAL_QUALITY_SCORE", "MIN_DISPLACEMENT_BODY_RATIO",
        "MIN_CANDLE_CLOSE_QUALITY", "MIN_MARKET_QUALITY_SCORE",
        "ADAPTIVE_WEIGHTS_ENABLED",
    ]
    raw = "|".join(f"{key}={os.getenv(key, '')}" for key in keys)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _read_forward_trades(path: Path = FORWARD_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _read_historical_trades(path: Path = HISTORICAL_TRADES_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _profit_values(rows: list[dict[str, Any]]) -> list[float]:
    return [_safe_float(row.get("profit")) for row in rows if abs(_safe_float(row.get("profit"))) > 1e-9]


def _r_values(rows: list[dict[str, Any]]) -> list[float]:
    values = []
    for row in rows:
        r_value = row.get("r")
        if r_value is None:
            risk = _safe_float(row.get("risk"))
            profit = _safe_float(row.get("profit"))
            if risk > 0:
                r_value = profit / risk
        parsed = _safe_float(r_value, math.nan)
        if math.isfinite(parsed):
            values.append(parsed)
    return values


def _confidence_interval(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"low": None, "high": None}
    mean = sum(values) / len(values)
    if len(values) < 2:
        return {"low": mean, "high": mean}
    margin = 1.96 * statistics.stdev(values) / math.sqrt(len(values))
    return {"low": mean - margin, "high": mean + margin}


def _max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    profits = _profit_values(rows)
    r_values = _r_values(rows)
    wins = [value for value in profits if value > 0]
    losses = [value for value in profits if value < 0]
    mean = sum(profits) / len(profits) if profits else 0.0
    if len(profits) > 1:
        sd = statistics.stdev(profits)
        sharpe = mean / sd * math.sqrt(len(profits)) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    return {
        "sample_size": len(profits),
        "net_profit": sum(profits),
        "win_rate": len(wins) / len(profits) if profits else 0.0,
        "expectancy": mean,
        "average_r": sum(r_values) / len(r_values) if r_values else 0.0,
        "rolling_r_multiple": r_values[-1] if r_values else 0.0,
        "sharpe": sharpe,
        "drawdown": _max_drawdown(profits),
        "confidence_interval": _confidence_interval(profits),
    }


def _rolling_metrics(rows: list[dict[str, Any]], window: int) -> list[dict[str, Any]]:
    output = []
    for index in range(len(rows)):
        chunk = rows[max(0, index - window + 1): index + 1]
        stats = _metrics(chunk)
        stats["end_timestamp"] = rows[index].get("timestamp")
        stats["window"] = len(chunk)
        output.append(stats)
    return output


def _stable_expectancy(rolling: list[dict[str, Any]], min_points: int = 3) -> bool:
    mature = [row for row in rolling if row.get("window", 0) >= min_points]
    if len(mature) < min_points:
        return False
    last = mature[-min_points:]
    return all(row.get("expectancy", 0.0) > 0 for row in last)


def _detect_degradation(rolling: list[dict[str, Any]], overall: dict[str, Any]) -> bool:
    if len(rolling) < 6:
        return False
    latest = rolling[-1]
    prior = rolling[-6]
    ci = latest.get("confidence_interval") or {}
    latest_drawdown = abs(_safe_float(latest.get("drawdown")))
    overall_drawdown = abs(_safe_float(overall.get("drawdown")))
    drawdown_worsened = overall_drawdown > 0 and latest_drawdown >= overall_drawdown * 1.25
    return (
        latest.get("expectancy", 0.0) < 0
        or latest.get("average_r", 0.0) < -0.10
        or (ci.get("high") is not None and ci.get("high") < 0)
        or drawdown_worsened
        or latest.get("expectancy", 0.0) < prior.get("expectancy", 0.0) * 0.5
    )


def _validation_status(sample_size: int, min_sample: int, expectancy: float, ci: dict[str, Any], stable_expectancy: bool, stable_ci: bool, degraded: bool) -> str:
    if degraded and sample_size >= max(10, min_sample // 2):
        return "DEGRADED"
    if sample_size < min_sample:
        return "PROMISING" if expectancy > 0 and (ci.get("low") or 0) >= 0 else "UNPROVEN"
    if expectancy > 0 and stable_expectancy and stable_ci:
        return "VALIDATED"
    if expectancy > 0:
        return "PROMISING"
    return "DEGRADED" if sample_size >= min_sample else "UNPROVEN"


def _group_metrics(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[str(row.get(field) or "unknown")].append(row)
    output = []
    for value, bucket in buckets.items():
        output.append({"value": value, **_metrics(bucket)})
    return sorted(output, key=lambda row: row["sample_size"], reverse=True)
