"""Signal-quality and break-even reports for Nexus.

Reads audit JSONL/CSV files produced by the scanner, engine, and replay
backtester.  This module does not add strategy inputs; it only summarizes what
the current signal path already produced.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"


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


def _read_csv(path: Path) -> list[dict[str, Any]]:
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


def score_bucket(score: float) -> str:
    if score < 0.20:
        return "0.00-0.20"
    if score < 0.40:
        return "0.20-0.40"
    if score < 0.60:
        return "0.40-0.60"
    if score < 0.80:
        return "0.60-0.80"
    return "0.80-1.00"


def build_score_distribution(rows: list[dict[str, Any]]) -> dict:
    buckets = Counter()
    for row in rows:
        buckets[score_bucket(_safe_float(row.get("score")))] += 1
    total = sum(buckets.values())
    ordered = ["0.00-0.20", "0.20-0.40", "0.40-0.60", "0.60-0.80", "0.80-1.00"]
    return {
        key: {
            "count": buckets.get(key, 0),
            "pct": round((buckets.get(key, 0) / total) * 100, 2) if total else 0.0,
        }
        for key in ordered
    }


def summarize_component_breakdown(rows: list[dict[str, Any]]) -> dict:
    fields = [
        "trend_score",
        "structure_score",
        "fvg_score",
        "liquidity_score",
        "volume_score",
        "session_score",
        "execution_score",
    ]
    output = {}
    for field in fields:
        values = [
            _safe_float((row.get("score_breakdown") or {}).get(field))
            for row in rows
            if isinstance(row.get("score_breakdown"), dict)
        ]
        output[field] = {
            "avg": round(sum(values) / len(values), 3) if values else 0.0,
            "above_0_5": sum(1 for value in values if value >= 0.5),
            "samples": len(values),
        }
    return output


def summarize_fvg_debug(rows: list[dict[str, Any]]) -> dict:
    rejected = [row for row in rows if not row.get("accepted")]
    reasons = Counter(str(row.get("rejection_reason") or "unknown") for row in rejected)
    accepted = [row for row in rows if row.get("accepted")]
    gap_sizes = [_safe_float(row.get("gap_size")) for row in accepted if row.get("gap_size") is not None]
    return {
        "total_checks": len(rows),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "avg_accepted_gap_size": round(sum(gap_sizes) / len(gap_sizes), 6) if gap_sizes else 0.0,
        "top_rejection_reasons": reasons.most_common(10),
        "visual_debug_path": str(DATA_DIR / "fvg_debug.jsonl"),
    }


def summarize_closed_trades(rows: list[dict[str, Any]]) -> dict:
    buckets = Counter()
    details = []
    for row in rows:
        reason = str(row.get("exit_reason") or row.get("reason") or "").upper()
        r_multiple = _safe_float(row.get("r_multiple"))
        near_be = abs(r_multiple) <= 0.10 or "BREAKEVEN" in reason or "BREAK_EVEN" in reason
        if near_be:
            buckets["stopped_at_break_even"] += 1
        if "PARTIAL" in reason:
            buckets["partial_profit_exit"] += 1
        if "TRAIL" in reason:
            buckets["trailing_stop_exit"] += 1
        if "REVERSE" in reason:
            buckets["reverse_profit_exit"] += 1
        details.append({
            "symbol": row.get("symbol"),
            "action": row.get("action"),
            "entry_price": row.get("entry"),
            "stop_loss": row.get("sl"),
            "take_profit": row.get("tp"),
            "highest_profit_reached": row.get("mfe_r"),
            "lowest_drawdown": row.get("mae_r"),
            "exit_reason": row.get("exit_reason") or row.get("reason"),
            "r_multiple": row.get("r_multiple"),
        })
    return {
        "counts": dict(buckets),
        "total_closed_trades": len(rows),
        "trade_details": details[-100:],
    }


def build_report(data_dir: Path = DATA_DIR) -> dict:
    signal_rows = _read_jsonl(data_dir / "signal_quality_audit.jsonl")
    fvg_rows = _read_jsonl(data_dir / "fvg_debug.jsonl")
    trades = _read_csv(data_dir / "trades.csv")
    return {
        "score_distribution": build_score_distribution(signal_rows),
        "component_breakdown": summarize_component_breakdown(signal_rows),
        "fvg_investigation": summarize_fvg_debug(fvg_rows),
        "break_even_investigation": summarize_closed_trades(trades),
    }


def main():
    parser = argparse.ArgumentParser(description="Generate Nexus signal-quality audit report.")
    parser.add_argument("--output", default=str(DATA_DIR / "signal_quality_report.json"))
    args = parser.parse_args()
    report = build_report()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Report written: {output}")
    print(json.dumps({
        "score_distribution": report["score_distribution"],
        "fvg_investigation": report["fvg_investigation"],
        "break_even_counts": report["break_even_investigation"]["counts"],
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
