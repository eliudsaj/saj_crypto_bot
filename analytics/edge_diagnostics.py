"""Statistical edge diagnostics for recorded strategy outcomes.

The diagnostics layer intentionally reads existing trade outcomes and strategy
journal metadata instead of duplicating strategy rules. Closed trades are
enriched with the nearest prior journal entry for the same symbol, then grouped
by components that can explain where expectancy is being gained or lost.
"""

from __future__ import annotations

import bisect
import csv
import json
import math
import os
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"


COMPONENT_FIELDS = [
    "symbol",
    "direction",
    "archetype",
    "trade_horizon",
    "session",
    "spread_state",
    "market_volatility",
    "displacement_quality",
    "liquidity_sweep_presence",
    "mss_bos_confirmation",
    "htf_alignment",
    "order_block_alignment",
    "fvg_presence",
    "premium_discount_alignment",
    "war_room_conviction_bucket",
]


def _json_safe(value: Any) -> Any:
    """Replace NaN/Infinity with JSON-safe nulls recursively."""
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


@dataclass
class Outcome:
    timestamp: datetime | None
    source: str
    symbol: str
    direction: str = "unknown"
    profit: float = 0.0
    r_multiple: float | None = None
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _bucket_number(value: Any, buckets: list[tuple[float, str]], default: str = "unknown") -> str:
    number = _safe_float(value, None)
    if number is None:
        return default
    for limit, label in buckets:
        if number < limit:
            return label
    return buckets[-1][1] if buckets else default


def _normalize_component_label(values: list[str], target: str) -> str:
    target_norm = target.lower()
    for value in values:
        if target_norm in str(value).lower():
            return "present"
    return "missing"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def _read_journal(path: Path = DATA_DIR / "strategy_journal.jsonl") -> dict[str, list[tuple[datetime, dict[str, Any]]]]:
    by_symbol: dict[str, list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
    for item in _read_jsonl(path):
        ts = _parse_time(item.get("timestamp"))
        symbol = str(item.get("symbol") or "").upper()
        if ts and symbol:
            by_symbol[symbol].append((ts, item))
    for symbol in by_symbol:
        by_symbol[symbol].sort(key=lambda row: row[0])
    return by_symbol


def _find_nearest_journal(
    journal: dict[str, list[tuple[datetime, dict[str, Any]]]],
    symbol: str,
    timestamp: datetime | None,
    direction: str = "",
) -> dict[str, Any]:
    if not timestamp:
        return {}
    rows = journal.get(str(symbol or "").upper(), [])
    if not rows:
        return {}
    times = [row[0] for row in rows]
    index = bisect.bisect_right(times, timestamp) - 1
    direction = str(direction or "").upper()
    for cursor in range(index, max(-1, index - 250), -1):
        item = rows[cursor][1]
        item_direction = str(item.get("direction") or "").upper()
        if not direction or not item_direction or item_direction == direction:
            return item
    return rows[index][1] if index >= 0 else {}


def _read_csv_outcomes(path: Path = DATA_DIR / "trades.csv") -> list[Outcome]:
    if not path.exists():
        return []
    outcomes: list[Outcome] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            profit = _safe_float(row.get("profit"), 0.0) or 0.0
            if abs(profit) <= 1e-9:
                continue
            outcomes.append(
                Outcome(
                    timestamp=_parse_time(row.get("exit_time") or row.get("timestamp")),
                    source="data/trades.csv",
                    symbol=str(row.get("symbol") or "unknown").upper(),
                    direction=str(row.get("action") or row.get("direction") or "unknown").upper(),
                    profit=profit,
                    r_multiple=_safe_float(row.get("r_multiple"), None),
                    reason=str(row.get("reason") or ""),
                )
            )
    return outcomes


def _read_log_outcomes(log_dir: Path = LOG_DIR) -> list[Outcome]:
    outcomes: list[Outcome] = []
    if not log_dir.exists():
        return outcomes
    for path in sorted(log_dir.glob("trades_*.json")):
        if ".corrupt-" in path.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict) or entry.get("event") != "TRADE_CLOSED":
                continue
            profit = _safe_float(entry.get("profit"), 0.0) or 0.0
            if abs(profit) <= 1e-9:
                continue
            risk = _safe_float(entry.get("risk"), 0.0) or 0.0
            raw_r = _safe_float(entry.get("r"), None)
            r_multiple = raw_r if raw_r is not None else (profit / risk if risk > 0 else None)
            outcomes.append(
                Outcome(
                    timestamp=_parse_time(entry.get("timestamp")),
                    source=str(path.relative_to(BASE_DIR)),
                    symbol=str(entry.get("symbol") or "unknown").upper(),
                    direction=str(entry.get("direction") or entry.get("action") or "unknown").upper(),
                    profit=profit,
                    r_multiple=r_multiple if r_multiple is not None and math.isfinite(r_multiple) else None,
                    reason=str(entry.get("reason") or ""),
                )
            )
    return outcomes


def _component_values(outcome: Outcome) -> dict[str, str]:
    meta = outcome.metadata or {}
    confirmed = [str(x) for x in meta.get("confirmed_components") or []]
    missing = [str(x) for x in meta.get("missing_blockers") or []]
    spread = meta.get("spread_state") or {}
    session = meta.get("session_quality") or {}

    conviction = _safe_float(meta.get("final_conviction"), None)
    session_score = _safe_float(session.get("score"), None)
    spread_safe = spread.get("safe")
    spread_pips = _safe_float(spread.get("spread_pips"), None)

    def component(label: str) -> str:
        if _normalize_component_label(confirmed, label) == "present":
            return "present"
        if _normalize_component_label(missing, label) == "present":
            return "missing"
        return "unknown"

    if spread_safe is True:
        spread_state = "safe"
    elif spread_safe is False:
        spread_state = "unsafe"
    else:
        spread_state = _bucket_number(spread_pips, [(1.0, "tight"), (2.0, "normal"), (999.0, "wide")])

    return {
        "symbol": outcome.symbol or "unknown",
        "direction": str(meta.get("direction") or outcome.direction or "unknown").upper(),
        "archetype": str(meta.get("archetype") or "unknown"),
        "trade_horizon": str(meta.get("trade_type") or "unknown").lower(),
        "session": str(session.get("label") or session.get("description") or _bucket_number(session_score, [(0.4, "low"), (0.7, "mixed"), (1.1, "strong")])),
        "spread_state": spread_state,
        "market_volatility": str(meta.get("market_volatility") or meta.get("volatility_state") or "unknown"),
        "displacement_quality": component("Displacement"),
        "liquidity_sweep_presence": component("Liquidity Sweep"),
        "mss_bos_confirmation": "present" if component("MSS") == "present" or component("BOS") == "present" else ("missing" if component("MSS") == "missing" or component("BOS") == "missing" else "unknown"),
        "htf_alignment": component("HTF"),
        "order_block_alignment": "present" if component("OB") == "present" or component("Order Block") == "present" else ("missing" if component("OB") == "missing" or component("Order Block") == "missing" else "unknown"),
        "fvg_presence": component("FVG"),
        "premium_discount_alignment": "present" if component("Premium") == "present" or component("Discount") == "present" else ("missing" if component("Premium") == "missing" or component("Discount") == "missing" else "unknown"),
        "war_room_conviction_bucket": _bucket_number(conviction, [(0.4, "<40%"), (0.55, "40-55%"), (0.7, "55-70%"), (1.1, "70%+")]),
    }


def _max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _mean_ci(values: list[float]) -> dict[str, float | None]:
    count = len(values)
    if count == 0:
        return {"low": None, "high": None}
    mean = sum(values) / count
    if count < 2:
        return {"low": mean, "high": mean}
    sd = statistics.stdev(values)
    margin = 1.96 * sd / math.sqrt(count)
    return {"low": mean - margin, "high": mean + margin}


def _confidence_quality(count: int, ci: dict[str, float | None]) -> str:
    low = ci.get("low")
    high = ci.get("high")
    if count < 10:
        return "low"
    if count < 30:
        return "medium"
    if low is not None and high is not None and (low > 0 or high < 0):
        return "high"
    return "medium"


def _metrics(rows: list[Outcome]) -> dict[str, Any]:
    profits = [row.profit for row in rows]
    r_values = [row.r_multiple for row in rows if row.r_multiple is not None and math.isfinite(row.r_multiple)]
    wins = [value for value in profits if value > 0]
    losses = [value for value in profits if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss else (None if gross_profit > 0 else 0.0)
    count = len(profits)
    mean = sum(profits) / count if count else 0.0
    sd = statistics.stdev(profits) if count > 1 else 0.0
    sharpe = (mean / sd * math.sqrt(count)) if sd > 0 and count > 1 else 0.0
    ci = _mean_ci(profits)
    return {
        "sample_size": count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / count if count else 0.0,
        "expectancy": mean,
        "average_r": sum(r_values) / len(r_values) if r_values else None,
        "sharpe": sharpe,
        "profit_factor": profit_factor,
        "net_profit": sum(profits),
        "drawdown_contribution": _max_drawdown(profits),
        "confidence_interval": ci,
        "confidence_quality": _confidence_quality(count, ci),
    }


def _enrich_outcomes(outcomes: list[Outcome], journal: dict[str, list[tuple[datetime, dict[str, Any]]]]) -> list[Outcome]:
    for outcome in outcomes:
        outcome.metadata = _find_nearest_journal(journal, outcome.symbol, outcome.timestamp, outcome.direction)
    return sorted(outcomes, key=lambda row: row.timestamp or datetime.min)


def _rank_component_groups(outcomes: list[Outcome], min_sample: int) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, dict[str, list[Outcome]]] = {field: defaultdict(list) for field in COMPONENT_FIELDS}
    for outcome in outcomes:
        values = _component_values(outcome)
        for field in COMPONENT_FIELDS:
            grouped[field][values.get(field, "unknown")].append(outcome)

    result: dict[str, list[dict[str, Any]]] = {}
    for field, buckets in grouped.items():
        rows = []
        for value, bucket_rows in buckets.items():
            stats = _metrics(bucket_rows)
            rows.append({"component": field, "value": value, **stats})
        rows.sort(key=lambda item: (item["expectancy"], item["sample_size"]), reverse=True)
        result[field] = rows
    return result


def _combination_rankings(outcomes: list[Outcome], min_sample: int, limit: int) -> dict[str, list[dict[str, Any]]]:
    combo_fields = [
        "symbol",
        "direction",
        "archetype",
        "trade_horizon",
        "session",
        "spread_state",
        "war_room_conviction_bucket",
        "liquidity_sweep_presence",
        "mss_bos_confirmation",
        "htf_alignment",
        "fvg_presence",
    ]
    buckets: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in outcomes:
        values = _component_values(outcome)
        for field_set in combinations(combo_fields, 3):
            key = " + ".join(f"{field}={values.get(field, 'unknown')}" for field in field_set)
            buckets[key].append(outcome)

    ranked = []
    for key, rows in buckets.items():
        if len(rows) < min_sample:
            continue
        stats = _metrics(rows)
        ranked.append({"combination": key, **stats})

    toxic = sorted(ranked, key=lambda item: (item["expectancy"], item["profit_factor"] or 0.0, -item["sample_size"]))[:limit]
    profitable_candidates = [
        item for item in ranked
        if item["expectancy"] > 0 and (item["profit_factor"] or 0.0) > 1
    ]
    profitable = sorted(
        profitable_candidates,
        key=lambda item: (item["expectancy"], item["profit_factor"] or 0.0, item["sample_size"]),
        reverse=True,
    )[:limit]
    return {"toxic_combinations": toxic, "profitable_combinations": profitable}


def build_edge_diagnostics(
    min_sample: int = 3,
    limit: int = 12,
    include_backtest_csv: bool = True,
) -> dict[str, Any]:
    """Build edge diagnostics from current runtime data files."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    journal = _read_journal()
    outcomes = _read_log_outcomes()
    if include_backtest_csv:
        outcomes.extend(_read_csv_outcomes())
    outcomes = _enrich_outcomes(outcomes, journal)

    component_rankings = _rank_component_groups(outcomes, min_sample)
    all_groups = [
        item
        for groups in component_rankings.values()
        for item in groups
        if item["sample_size"] >= min_sample
    ]
    strongest = sorted(all_groups, key=lambda item: (item["expectancy"], item["profit_factor"] or 0.0, item["sample_size"]), reverse=True)[:limit]
    weakest = sorted(all_groups, key=lambda item: (item["expectancy"], item["profit_factor"] or 0.0, -item["sample_size"]))[:limit]
    combos = _combination_rankings(outcomes, min_sample=min_sample, limit=limit)

    return _json_safe({
        "summary": {
            "closed_trades": len(outcomes),
            "matched_journal_trades": sum(1 for row in outcomes if row.metadata),
            "unmatched_trades": sum(1 for row in outcomes if not row.metadata),
            "min_sample": min_sample,
            "sources": sorted(set(row.source for row in outcomes)),
        },
        "overall": _metrics(outcomes),
        "components": component_rankings,
        "strongest_edge_contributors": strongest,
        "weakest_edge_contributors": weakest,
        **combos,
    })
