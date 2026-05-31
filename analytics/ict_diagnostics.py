"""ICT blocker and detector diagnostics.

These helpers write flat CSV/JSON reports that explain why strict ICT mode is
blocking setups. They do not change strategy decisions.
"""

from __future__ import annotations

import csv
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from strategy.ict import detect_fvgs, detect_liquidity, detect_market_structure, normalize_candles, pip_size


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
BLOCKER_REPORT = DATA_DIR / "ict_blocker_report.csv"
BLOCKER_SUMMARY = DATA_DIR / "ict_blocker_summary.json"
NEAR_MISS_REPORT = DATA_DIR / "ict_near_miss_setups.csv"
FVG_RETEST_AUDIT = DATA_DIR / "fvg_retest_audit.csv"
LIQUIDITY_SWEEP_AUDIT = DATA_DIR / "liquidity_sweep_audit.csv"
STRUCTURE_AUDIT = DATA_DIR / "structure_audit.csv"

ICT_SESSION_WINDOWS = [
    {"name": "Asia", "start_hour": 0, "end_hour": 6},
    {"name": "London", "start_hour": 7, "end_hour": 11},
    {"name": "NewYork", "start_hour": 12, "end_hour": 16},
    {"name": "NewYorkContinuation", "start_hour": 16, "end_hour": 20},
]

REQUIREMENT_FIELDS = [
    "htf_bias_aligned",
    "liquidity_sweep",
    "bos_confirmed",
    "choch_confirmed",
    "fvg_present",
    "fvg_retested",
    "order_block_valid",
    "session_allowed",
    "spread_safe",
    "rr_valid",
    "setup_score_valid",
    "conviction_valid",
]

PASS_IF_ANY = [("bos_or_choch", ["bos_confirmed", "choch_confirmed"])]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def _append_csv(path: Path, row: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    fieldnames = list(row.keys())
    if exists:
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                fieldnames = next(reader)
        except Exception:
            pass
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(_json_safe(row))


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def reset_reports():
    for path in [BLOCKER_REPORT, BLOCKER_SUMMARY, NEAR_MISS_REPORT, FVG_RETEST_AUDIT, LIQUIDITY_SWEEP_AUDIT, STRUCTURE_AUDIT]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    ensure_report_files()


def _write_header(path: Path, fieldnames: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()


def ensure_report_files():
    _write_header(BLOCKER_REPORT, [
        "time", "symbol", "direction", "setup_type", "timeframe", *REQUIREMENT_FIELDS,
        "passed", "blocked", "passed_count", "failed_count", "failed_reasons",
        "setup_score", "conviction", "rr", "session", "spread_pips",
    ])
    _write_header(NEAR_MISS_REPORT, [
        "symbol", "time", "direction", "passed_count", "failed_count", "failed_reasons",
        "setup_score", "conviction", "rr", "session", "spread_pips",
    ])
    _write_header(FVG_RETEST_AUDIT, [
        "symbol", "timeframe", "fvg_direction", "fvg_top", "fvg_bottom", "fvg_size_pips",
        "candle_created_time", "whether_price_returned", "retest_time",
        "retest_depth_percent", "whether_entry_would_trigger",
    ])
    _write_header(LIQUIDITY_SWEEP_AUDIT, [
        "symbol", "timeframe", "time", "level_swept", "sweep_direction", "wick_size",
        "wick_size_pips", "close_back_inside_range", "confirmation_candle",
        "valid_or_invalid", "rejection_reason",
    ])
    _write_header(STRUCTURE_AUDIT, [
        "symbol", "timeframe", "time", "previous_swing_high", "previous_swing_low",
        "break_direction", "candle_close", "wick_only_or_close_break",
        "valid_bos", "valid_choch", "rejection_reason",
    ])


def expected_r(signal: dict) -> float | None:
    try:
        entry = float(signal.get("entry"))
        sl = float(signal.get("sl"))
        tp = float(signal.get("tp"))
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        return abs(tp - entry) / risk
    except Exception:
        return None


def _timeframe_label(timeframe: Any) -> str:
    mapping = {1: "M1", 5: "M5", 15: "M15", 30: "M30", 60: "H1", 240: "H4"}
    return mapping.get(timeframe, str(timeframe or "M5"))


def record_blocker(signal: dict, ensemble_decision: dict | None = None, timeframe: Any = None) -> dict:
    ict = signal.get("ict") or {}
    if not ict:
        return {}
    components = ict.get("components") or {}
    setup = signal.get("setup_score") or {}
    spread = signal.get("spread_safety") or setup.get("spread") or {}
    rr = expected_r(signal)
    min_setup = _safe_float(os.getenv("MIN_SETUP_SCORE", 0.80), 0.80)
    min_conviction = _safe_float(os.getenv("MIN_CONVICTION", 0.70), 0.70)
    min_rr = _safe_float(os.getenv("MIN_RR", os.getenv("ICT_MIN_RISK_REWARD", 1.5)), 1.5)
    allowed_sessions = {
        item.strip().replace(" ", "")
        for item in os.getenv("ICT_ALLOWED_SESSIONS", "London,NewYork").split(",")
        if item.strip()
    }
    session = str(ict.get("session_name") or "Unknown").replace(" ", "")
    setup_score = _safe_float(setup.get("score"))
    conviction = _safe_float((ensemble_decision or {}).get("conviction") or signal.get("conviction"))

    checks = {
        "htf_bias_aligned": bool(components.get("htf_bias_agrees")),
        "liquidity_sweep": bool(components.get("liquidity_sweep_detected")),
        "bos_confirmed": bool(components.get("bos_detected")),
        "choch_confirmed": bool(components.get("choch_detected")),
        "fvg_present": bool(components.get("fvg_present")),
        "fvg_retested": bool(components.get("fvg_retest_detected")),
        "order_block_valid": bool(components.get("order_block_valid")),
        "session_allowed": session in allowed_sessions if allowed_sessions else True,
        "spread_safe": spread.get("safe") is not False,
        "rr_valid": rr is not None and rr >= min_rr,
        "setup_score_valid": setup_score >= min_setup,
        "conviction_valid": conviction >= min_conviction,
    }

    failed = [key for key, value in checks.items() if not value]
    # Order block validity is diagnostic here; strict requested trade entry is FVG-retest based.
    failed = [key for key in failed if key != "order_block_valid"]
    # BOS and CHOCH are alternatives for the same structure requirement.
    if checks["bos_confirmed"] or checks["choch_confirmed"]:
        failed = [key for key in failed if key not in ["bos_confirmed", "choch_confirmed"]]
    elif "bos_confirmed" in failed and "choch_confirmed" in failed:
        failed = [key for key in failed if key not in ["bos_confirmed", "choch_confirmed"]]
        failed.append("bos_or_choch")
    passed_count = len(REQUIREMENT_FIELDS) - len(failed)
    row = {
        "time": signal.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "symbol": signal.get("symbol"),
        "direction": signal.get("action"),
        "setup_type": signal.get("type") or setup.get("archetype") or signal.get("nature"),
        "timeframe": _timeframe_label(timeframe),
        **checks,
        "passed": len(failed) == 0,
        "blocked": len(failed) > 0,
        "passed_count": passed_count,
        "failed_count": len(failed),
        "failed_reasons": ";".join(failed),
        "setup_score": setup_score,
        "conviction": conviction,
        "rr": rr,
        "session": session,
        "spread_pips": spread.get("spread_pips"),
    }
    _append_csv(BLOCKER_REPORT, row)
    if 1 <= len(failed) <= 2:
        _append_csv(NEAR_MISS_REPORT, {
            "symbol": row["symbol"],
            "time": row["time"],
            "direction": row["direction"],
            "passed_count": row["passed_count"],
            "failed_count": row["failed_count"],
            "failed_reasons": row["failed_reasons"],
            "setup_score": row["setup_score"],
            "conviction": row["conviction"],
            "rr": row["rr"],
            "session": row["session"],
            "spread_pips": row["spread_pips"],
        })
    return row


def audit_detectors(candles: pd.DataFrame, symbol: str, action: str | None = None, timeframe: Any = None):
    df = normalize_candles(candles)
    if df.empty:
        return
    direction = "Bullish" if str(action).upper() == "BUY" else "Bearish" if str(action).upper() == "SELL" else None
    _audit_fvgs(df, symbol, direction, timeframe)
    _audit_liquidity(df, symbol, timeframe)
    _audit_structure(df, symbol, timeframe)


def _audit_fvgs(df: pd.DataFrame, symbol: str, direction: str | None, timeframe: Any):
    gaps = detect_fvgs(df, symbol)
    if not gaps:
        return
    latest = df.iloc[-1]
    latest_time = latest.get("time")
    for gap in gaps[-8:]:
        if direction and gap.get("direction") != direction:
            continue
        zone_low, zone_high = sorted(gap["zone"])
        future = df.iloc[int(gap["index"]) + 1:]
        returned = False
        retest_time = None
        retest_depth = None
        for _, candle in future.iterrows():
            high = float(candle["high"])
            low = float(candle["low"])
            if high >= zone_low and low <= zone_high:
                returned = True
                retest_time = candle.get("time")
                if gap["direction"] == "Bullish":
                    depth = (zone_high - max(low, zone_low)) / max(zone_high - zone_low, 1e-12)
                else:
                    depth = (min(high, zone_high) - zone_low) / max(zone_high - zone_low, 1e-12)
                retest_depth = round(max(0.0, min(1.0, depth)) * 100, 2)
                break
        _append_csv(FVG_RETEST_AUDIT, {
            "symbol": symbol,
            "timeframe": _timeframe_label(timeframe),
            "fvg_direction": gap.get("direction"),
            "fvg_top": zone_high,
            "fvg_bottom": zone_low,
            "fvg_size_pips": round(float(gap.get("size") or 0) / pip_size(symbol), 3),
            "candle_created_time": df.iloc[int(gap["index"])].get("time"),
            "whether_price_returned": returned,
            "retest_time": retest_time,
            "retest_depth_percent": retest_depth,
            "whether_entry_would_trigger": returned and str(latest_time) == str(retest_time),
        })


def _audit_liquidity(df: pd.DataFrame, symbol: str, timeframe: Any):
    if len(df) < 12:
        return
    tolerance = pip_size(symbol) * _safe_float(os.getenv("ICT_EQUAL_LEVEL_TOLERANCE_PIPS", 2.0), 2.0)
    start = max(1, len(df) - 8)
    for idx in range(start, len(df)):
        candle = df.iloc[idx]
        base = df.iloc[max(0, idx - 20):idx]
        if base.empty:
            continue
        base_high = float(base["high"].max())
        base_low = float(base["low"].min())
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        open_price = float(candle["open"])
        rows = []
        if high > base_high + tolerance:
            valid = close < base_high
            rows.append(("Bearish", base_high, high - max(open_price, close), valid, "close did not return inside range" if not valid else ""))
        if low < base_low - tolerance:
            valid = close > base_low
            rows.append(("Bullish", base_low, min(open_price, close) - low, valid, "close did not return inside range" if not valid else ""))
        for sweep_direction, level, wick_size, valid, reason in rows:
            _append_csv(LIQUIDITY_SWEEP_AUDIT, {
                "symbol": symbol,
                "timeframe": _timeframe_label(timeframe),
                "time": candle.get("time"),
                "level_swept": level,
                "sweep_direction": sweep_direction,
                "wick_size": wick_size,
                "wick_size_pips": round(wick_size / pip_size(symbol), 3),
                "close_back_inside_range": valid,
                "confirmation_candle": candle.get("time"),
                "valid_or_invalid": "valid" if valid else "invalid",
                "rejection_reason": reason,
            })


def _audit_structure(df: pd.DataFrame, symbol: str, timeframe: Any):
    if len(df) < 12:
        return
    start = max(5, len(df) - 8)
    for idx in range(start, len(df)):
        window = df.iloc[:idx + 1]
        structure = detect_market_structure(window)
        candle = window.iloc[-1]
        highs = structure.get("swing_highs") or []
        lows = structure.get("swing_lows") or []
        prev_high = highs[-1]["price"] if highs else None
        prev_low = lows[-1]["price"] if lows else None
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        rows = []
        if prev_high is not None and high > prev_high:
            close_break = close > prev_high
            rows.append(("Bullish", close_break, structure.get("bos"), structure.get("choch"), "wick-only break" if not close_break else ""))
        if prev_low is not None and low < prev_low:
            close_break = close < prev_low
            rows.append(("Bearish", close_break, structure.get("bos"), structure.get("choch"), "wick-only break" if not close_break else ""))
        for break_direction, close_break, bos, choch, reason in rows:
            _append_csv(STRUCTURE_AUDIT, {
                "symbol": symbol,
                "timeframe": _timeframe_label(timeframe),
                "time": candle.get("time"),
                "previous_swing_high": prev_high,
                "previous_swing_low": prev_low,
                "break_direction": break_direction,
                "candle_close": close,
                "wick_only_or_close_break": "close_break" if close_break else "wick_only",
                "valid_bos": bool(bos and bos.get("direction") == break_direction),
                "valid_choch": bool(choch and choch.get("direction") == break_direction),
                "rejection_reason": reason,
            })


def build_summary() -> dict:
    rows = _read_csv(BLOCKER_REPORT)
    blocked = [row for row in rows if str(row.get("blocked")).lower() == "true"]
    passed = [row for row in rows if str(row.get("passed")).lower() == "true"]
    reason_counts = Counter()
    by_symbol: dict[str, Counter] = defaultdict(Counter)
    by_session: dict[str, Counter] = defaultdict(Counter)
    by_timeframe: dict[str, Counter] = defaultdict(Counter)
    for row in blocked:
        reasons = [
            item for item in str(row.get("failed_reasons") or "").split(";")
            if item and item != "order_block_valid"
        ]
        symbol = str(row.get("symbol") or "unknown")
        session = str(row.get("session") or "unknown")
        timeframe = str(row.get("timeframe") or "unknown")
        for reason in reasons:
            reason_counts[reason] += 1
            by_symbol[symbol][reason] += 1
            by_session[session][reason] += 1
            by_timeframe[timeframe][reason] += 1
    common = reason_counts.most_common(2)
    summary = {
        "total_scanned_setups": len(rows),
        "total_blocked_setups": len(blocked),
        "total_passed_setups": len(passed),
        "most_common_blocker": common[0] if len(common) > 0 else None,
        "second_most_common_blocker": common[1] if len(common) > 1 else None,
        "blocker_count_by_symbol": {key: dict(value) for key, value in by_symbol.items()},
        "blocker_count_by_session": {key: dict(value) for key, value in by_session.items()},
        "blocker_count_by_timeframe": {key: dict(value) for key, value in by_timeframe.items()},
        "recommendation": recommendation(rows, reason_counts),
    }
    BLOCKER_SUMMARY.write_text(json.dumps(_json_safe(summary), indent=2, default=str), encoding="utf-8")
    return summary


def recommendation(rows: list[dict[str, Any]], reason_counts: Counter) -> dict:
    if not rows:
        return {
            "condition": None,
            "message": "No ICT blocker rows were recorded. Run backtest with --diagnostics.",
            "appears_too_strict": False,
            "detector_may_be_broken": False,
            "recommended_next_step": "Confirm scanner emits FVG candidates during replay.",
        }
    total = len(rows)
    top, count = reason_counts.most_common(1)[0] if reason_counts else (None, 0)
    pct = (count / total) * 100 if total else 0
    fvg_rows = _read_csv(FVG_RETEST_AUDIT)
    fvg_detected = len(fvg_rows)
    fvg_retested = sum(1 for row in fvg_rows if str(row.get("whether_price_returned")).lower() == "true")
    message = f"{top} blocks {pct:.1f}% of setups." if top else "No dominant blocker."
    detector_may_be_broken = False
    appears_too_strict = pct >= 80
    next_step = "Review the top blocker against chart samples before changing thresholds."
    if top == "fvg_retested":
        message += f" {fvg_detected} FVGs were audited and {fvg_retested} retests were recognized."
        detector_may_be_broken = fvg_detected > 10 and fvg_retested == 0
        next_step = "Review FVG retest tolerance and whether retests are checked after the gap creation candle."
    elif top == "liquidity_sweep":
        sweeps = _read_csv(LIQUIDITY_SWEEP_AUDIT)
        valid = sum(1 for row in sweeps if str(row.get("valid_or_invalid")).lower() == "valid")
        message += f" Liquidity audit found {len(sweeps)} potential sweeps and {valid} valid sweeps."
        detector_may_be_broken = len(sweeps) == 0 and total > 20
        next_step = "Review sweep lookback/tolerance and close-back-inside rule."
    elif top in ["bos_confirmed", "choch_confirmed", "bos_or_choch"]:
        structures = _read_csv(STRUCTURE_AUDIT)
        valid = sum(1 for row in structures if str(row.get("valid_bos")).lower() == "true" or str(row.get("valid_choch")).lower() == "true")
        message += f" Structure audit found {len(structures)} breaks and {valid} valid BOS/CHoCH events."
        detector_may_be_broken = len(structures) == 0 and total > 20
        next_step = "Review swing detection lookback and close-break requirement."
    return {
        "condition": top,
        "message": message,
        "appears_too_strict": appears_too_strict,
        "detector_may_be_broken": detector_may_be_broken,
        "recommended_next_step": next_step,
    }


def load_dashboard_payload(limit: int = 50) -> dict:
    summary = build_summary() if BLOCKER_REPORT.exists() else {}
    near_misses = _read_csv(NEAR_MISS_REPORT)[-limit:]
    allowed_sessions = os.getenv("ICT_ALLOWED_SESSIONS", "London,NewYork")
    return {
        "summary": summary,
        "near_misses": near_misses,
        "symbol_breakdown": summary.get("blocker_count_by_symbol", {}),
        "session_breakdown": summary.get("blocker_count_by_session", {}),
        "timeframe_breakdown": summary.get("blocker_count_by_timeframe", {}),
        "session_clock": session_clock(allowed_sessions),
        "gate_config": {
            "ict_enabled": os.getenv("ICT_ENABLED", "false").lower() in ["true", "1", "yes"],
            "require_liquidity_sweep": os.getenv("ICT_REQUIRE_LIQUIDITY_SWEEP", "true").lower() in ["true", "1", "yes"],
            "require_fvg_retest": os.getenv("ICT_REQUIRE_FVG_RETEST", "true").lower() in ["true", "1", "yes"],
            "require_bos_or_choch": os.getenv("ICT_REQUIRE_BOS_OR_CHOCH", "true").lower() in ["true", "1", "yes"],
            "wait_for_retest": os.getenv("WAIT_FOR_RETEST", "true").lower() in ["true", "1", "yes"],
            "early_entry_enabled": os.getenv("FEATURE_EARLY_ENTRY", "false").lower() in ["true", "1", "yes"],
            "min_setup_score": os.getenv("MIN_SETUP_SCORE", "0.80"),
            "min_conviction": os.getenv("MIN_CONVICTION", "0.70"),
            "min_rr": os.getenv("MIN_RR", os.getenv("ICT_MIN_RISK_REWARD", "1.5")),
            "allowed_sessions": allowed_sessions,
        },
    }


def _normalize_session(value: Any) -> str:
    return str(value or "").strip().replace(" ", "")


def _session_for_hour(hour: int) -> str:
    for window in ICT_SESSION_WINDOWS:
        if window["start_hour"] <= hour < window["end_hour"]:
            return window["name"]
    return "Transition"


def _window_time(day: datetime, hour: int) -> datetime:
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


def session_clock(allowed_sessions: str | None = None, now: datetime | None = None) -> dict:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local_now = now_utc.astimezone()
    allowed = {
        _normalize_session(item)
        for item in str(allowed_sessions or "London,NewYork").split(",")
        if item.strip()
    }
    current_session = _session_for_hour(now_utc.hour)
    candidates = []
    today = now_utc.replace(minute=0, second=0, microsecond=0)
    for day_offset in range(0, 3):
        day = today + timedelta(days=day_offset)
        for window in ICT_SESSION_WINDOWS:
            if allowed and _normalize_session(window["name"]) not in allowed:
                continue
            start = _window_time(day, int(window["start_hour"]))
            end = _window_time(day, int(window["end_hour"]))
            if now_utc < end:
                candidates.append({"name": window["name"], "start": start, "end": end})
    next_allowed = None
    for item in sorted(candidates, key=lambda row: row["start"]):
        if now_utc < item["end"]:
            if now_utc >= item["start"]:
                seconds = 0
            else:
                seconds = int((item["start"] - now_utc).total_seconds())
            next_allowed = {
                "name": item["name"],
                "starts_at_utc": item["start"].isoformat(),
                "ends_at_utc": item["end"].isoformat(),
                "seconds_until_start": seconds,
                "minutes_until_start": round(seconds / 60, 1),
            }
            break
    schedule = [
        {
            "name": window["name"],
            "start_utc": f"{int(window['start_hour']):02d}:00",
            "end_utc": f"{int(window['end_hour']):02d}:00",
            "allowed": _normalize_session(window["name"]) in allowed if allowed else True,
        }
        for window in ICT_SESSION_WINDOWS
    ]
    return {
        "basis": "UTC",
        "server_time_utc": now_utc.isoformat(),
        "server_time_local": local_now.isoformat(),
        "local_timezone": local_now.tzname(),
        "detected_session": current_session,
        "current_session_allowed": _normalize_session(current_session) in allowed if allowed else True,
        "allowed_sessions": sorted(allowed),
        "next_allowed_session": next_allowed,
        "schedule": schedule,
    }
