"""ICT-style setup detection helpers.

The functions in this module are intentionally data-frame first so they can be
unit tested without MT5.  Live/replay adapters can pass recent candles from any
source and receive a plain dict of components and blockers.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

import pandas as pd


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pip_size(symbol: str) -> float:
    clean = str(symbol or "").upper()
    if "XAU" in clean or "GOLD" in clean:
        return 0.1
    if clean.endswith("JPY"):
        return 0.01
    return 0.0001


def min_fvg_size(symbol: str) -> float:
    key = "ICT_MIN_FVG_PIPS_" + "".join(ch for ch in str(symbol or "").upper() if ch.isalnum())
    pips = _safe_float(os.getenv(key), None)
    if pips is None:
        clean = str(symbol or "").upper()
        if "XAU" in clean or "GOLD" in clean:
            pips = _safe_float(os.getenv("ICT_MIN_FVG_PIPS_XAU", 5.0), 5.0)
        elif clean.endswith("JPY"):
            pips = _safe_float(os.getenv("ICT_MIN_FVG_PIPS_JPY", 0.8), 0.8)
        else:
            pips = _safe_float(os.getenv("ICT_MIN_FVG_PIPS_FX", 1.0), 1.0)
    return pips * pip_size(symbol)


def normalize_candles(candles: pd.DataFrame) -> pd.DataFrame:
    if candles is None or candles.empty:
        return pd.DataFrame()
    df = candles.copy()
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "time" in df:
        df = df.sort_values("time")
    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def detect_swings(candles: pd.DataFrame, lookback: int = 2) -> dict[str, list[dict]]:
    df = normalize_candles(candles)
    highs: list[dict] = []
    lows: list[dict] = []
    if len(df) < lookback * 2 + 3:
        return {"swing_highs": highs, "swing_lows": lows}

    for i in range(lookback, len(df) - lookback):
        window = df.iloc[i - lookback:i + lookback + 1]
        high = float(df.loc[i, "high"])
        low = float(df.loc[i, "low"])
        if high >= float(window["high"].max()):
            highs.append({"index": i, "time": df.loc[i].get("time"), "price": high})
        if low <= float(window["low"].min()):
            lows.append({"index": i, "time": df.loc[i].get("time"), "price": low})
    return {"swing_highs": highs, "swing_lows": lows}


def detect_market_structure(candles: pd.DataFrame) -> dict:
    df = normalize_candles(candles)
    swings = detect_swings(df)
    highs = swings["swing_highs"]
    lows = swings["swing_lows"]
    latest_close = float(df.iloc[-1]["close"]) if not df.empty else 0.0
    prior_high = next((x for x in reversed(highs) if x["index"] < len(df) - 1), None)
    prior_low = next((x for x in reversed(lows) if x["index"] < len(df) - 1), None)

    bias = "Neutral"
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1]["price"] > highs[-2]["price"] and lows[-1]["price"] > lows[-2]["price"]:
            bias = "Bullish"
        elif highs[-1]["price"] < highs[-2]["price"] and lows[-1]["price"] < lows[-2]["price"]:
            bias = "Bearish"

    bos = None
    choch = None
    if prior_high and latest_close > prior_high["price"]:
        event = {"direction": "Bullish", "level": prior_high["price"], "type": "BOS" if bias in ["Bullish", "Neutral"] else "CHoCH"}
        bos = event if event["type"] == "BOS" else None
        choch = event if event["type"] == "CHoCH" else None
        bias = "Bullish"
    elif prior_low and latest_close < prior_low["price"]:
        event = {"direction": "Bearish", "level": prior_low["price"], "type": "BOS" if bias in ["Bearish", "Neutral"] else "CHoCH"}
        bos = event if event["type"] == "BOS" else None
        choch = event if event["type"] == "CHoCH" else None
        bias = "Bearish"

    return {
        **swings,
        "bias": bias,
        "bos": bos,
        "choch": choch,
        "bos_detected": bool(bos),
        "choch_detected": bool(choch),
    }


def detect_liquidity(candles: pd.DataFrame, symbol: str) -> dict:
    df = normalize_candles(candles)
    if len(df) < 12:
        return {"equal_highs": [], "equal_lows": [], "sweep": None, "buy_side_liquidity": None, "sell_side_liquidity": None}
    tolerance = pip_size(symbol) * _safe_float(os.getenv("ICT_EQUAL_LEVEL_TOLERANCE_PIPS", 2.0), 2.0)
    recent = df.iloc[-20:-1] if len(df) > 20 else df.iloc[:-1]
    latest = df.iloc[-1]

    equal_highs = []
    equal_lows = []
    highs = recent["high"].tolist()
    lows = recent["low"].tolist()
    for i in range(1, len(highs)):
        if abs(float(highs[i]) - float(highs[i - 1])) <= tolerance:
            equal_highs.append({"price": max(float(highs[i]), float(highs[i - 1])), "indexes": [i - 1, i]})
        if abs(float(lows[i]) - float(lows[i - 1])) <= tolerance:
            equal_lows.append({"price": min(float(lows[i]), float(lows[i - 1])), "indexes": [i - 1, i]})

    prior_high = float(recent["high"].max())
    prior_low = float(recent["low"].min())
    sweep = None
    sweep_start = max(1, len(df) - int(_safe_float(os.getenv("ICT_SWEEP_LOOKBACK_BARS", 5), 5)))
    for idx in range(sweep_start, len(df)):
        candidate = df.iloc[idx]
        base = df.iloc[max(0, idx - 20):idx]
        if base.empty:
            continue
        base_high = float(base["high"].max())
        base_low = float(base["low"].min())
        close = float(candidate["close"])
        if float(candidate["high"]) > base_high + tolerance and close < base_high:
            sweep = {"direction": "Bearish", "side": "buy-side", "level": base_high, "swept_price": float(candidate["high"]), "index": idx}
        elif float(candidate["low"]) < base_low - tolerance and close > base_low:
            sweep = {"direction": "Bullish", "side": "sell-side", "level": base_low, "swept_price": float(candidate["low"]), "index": idx}

    return {
        "equal_highs": equal_highs,
        "equal_lows": equal_lows,
        "sweep": sweep,
        "liquidity_sweep_detected": bool(sweep),
        "buy_side_liquidity": equal_highs[-1]["price"] if equal_highs else prior_high,
        "sell_side_liquidity": equal_lows[-1]["price"] if equal_lows else prior_low,
    }


def detect_fvgs(candles: pd.DataFrame, symbol: str) -> list[dict]:
    df = normalize_candles(candles)
    gaps: list[dict] = []
    minimum = min_fvg_size(symbol)
    if len(df) < 3:
        return gaps
    for i in range(2, len(df)):
        a = df.iloc[i - 2]
        c = df.iloc[i]
        if float(c["low"]) > float(a["high"]):
            size = float(c["low"]) - float(a["high"])
            if size >= minimum:
                gaps.append({"direction": "Bullish", "index": i, "zone": (float(a["high"]), float(c["low"])), "size": size})
        if float(c["high"]) < float(a["low"]):
            size = float(a["low"]) - float(c["high"])
            if size >= minimum:
                gaps.append({"direction": "Bearish", "index": i, "zone": (float(c["high"]), float(a["low"])), "size": size})
    return gaps


def detect_fvg_retest(candles: pd.DataFrame, symbol: str, direction: str | None = None) -> dict | None:
    df = normalize_candles(candles)
    if len(df) < 4:
        return None
    latest = df.iloc[-1]
    low = float(latest["low"])
    high = float(latest["high"])
    for gap in reversed(detect_fvgs(df.iloc[:-1], symbol)):
        if direction and gap["direction"] != direction:
            continue
        zone_low, zone_high = sorted(gap["zone"])
        if high >= zone_low and low <= zone_high:
            return {**gap, "retest_detected": True, "retest_price": float(latest["close"])}
    return None


def detect_order_block(candles: pd.DataFrame, structure: dict, symbol: str) -> dict | None:
    df = normalize_candles(candles)
    event = structure.get("bos") or structure.get("choch")
    if len(df) < 8 or not event:
        return None
    direction = event.get("direction")
    min_ratio = _safe_float(os.getenv("ICT_MIN_DISPLACEMENT_BODY_RATIO", 1.5), 1.5)
    latest = df.iloc[-1]
    prior = df.iloc[-12:-1]
    avg_body = (prior["close"] - prior["open"]).abs().mean()
    body = abs(float(latest["close"]) - float(latest["open"]))
    if avg_body <= 0 or body / avg_body < min_ratio:
        return None

    search = df.iloc[-8:-1]
    if direction == "Bullish":
        opposite = search[search["close"] < search["open"]]
        if opposite.empty:
            return None
        candle = opposite.iloc[-1]
        return {"type": "BUY", "direction": "Bullish", "zone": (float(candle["low"]), float(candle["high"])), "valid": True, "displacement_ratio": round(float(body / avg_body), 2)}
    if direction == "Bearish":
        opposite = search[search["close"] > search["open"]]
        if opposite.empty:
            return None
        candle = opposite.iloc[-1]
        return {"type": "SELL", "direction": "Bearish", "zone": (float(candle["low"]), float(candle["high"])), "valid": True, "displacement_ratio": round(float(body / avg_body), 2)}
    return None


def session_name(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    hour = now.astimezone(timezone.utc).hour
    if 7 <= hour < 11:
        return "London"
    if 12 <= hour < 16:
        return "NewYork"
    if 16 <= hour < 20:
        return "NewYorkContinuation"
    if 0 <= hour < 6:
        return "Asia"
    return "Transition"


def premium_discount(candles: pd.DataFrame, entry_price: float | None = None) -> dict:
    df = normalize_candles(candles)
    if df.empty or entry_price is None:
        return {"zone": "unknown", "midpoint": None, "range_high": None, "range_low": None}
    recent = df.tail(40)
    range_high = float(recent["high"].max())
    range_low = float(recent["low"].min())
    midpoint = (range_high + range_low) / 2.0
    entry = float(entry_price)
    if entry > midpoint:
        zone = "premium"
    elif entry < midpoint:
        zone = "discount"
    else:
        zone = "equilibrium"
    return {
        "zone": zone,
        "midpoint": midpoint,
        "range_high": range_high,
        "range_low": range_low,
        "entry_price": entry,
        "inside_premium": zone == "premium",
        "inside_discount": zone == "discount",
    }


def analyze_ict(candles: pd.DataFrame, symbol: str, action: str | None = None, htf_bias: dict | None = None, now: datetime | None = None, entry_price: float | None = None) -> dict:
    df = normalize_candles(candles)
    direction = "Bullish" if str(action).upper() == "BUY" else "Bearish" if str(action).upper() == "SELL" else None
    structure = detect_market_structure(df)
    liquidity = detect_liquidity(df, symbol)
    fvgs = detect_fvgs(df, symbol)
    fvg_retest = detect_fvg_retest(df, symbol, direction)
    order_block = detect_order_block(df, structure, symbol)
    pd_zone = premium_discount(df, entry_price)
    session = session_name(now)
    htf_direction = (htf_bias or {}).get("direction")
    htf_agrees = direction is None or htf_direction in [None, "Neutral", direction]
    bos_or_choch = structure.get("bos") or structure.get("choch")

    components = {
        "htf_bias_agrees": bool(htf_agrees),
        "liquidity_sweep_detected": bool(liquidity.get("sweep") and (direction is None or liquidity["sweep"].get("direction") == direction)),
        "bos_detected": bool(structure.get("bos") and (direction is None or structure["bos"].get("direction") == direction)),
        "choch_detected": bool(structure.get("choch") and (direction is None or structure["choch"].get("direction") == direction)),
        "fvg_retest_detected": bool(fvg_retest),
        "fvg_present": bool([gap for gap in fvgs if direction is None or gap.get("direction") == direction]),
        "order_block_valid": bool(order_block and order_block.get("valid") and (direction is None or order_block.get("direction") == direction)),
    }
    components["bos_or_choch_detected"] = bool(components["bos_detected"] or components["choch_detected"])
    return {
        "strategy_type": "ICT",
        "symbol": symbol,
        "action": action,
        "bias": structure.get("bias"),
        "higher_timeframe_bias": htf_bias,
        "session_name": session,
        "structure": structure,
        "liquidity": liquidity,
        "fvgs": fvgs,
        "fvg_retest": fvg_retest,
        "order_block": order_block,
        "premium_discount": pd_zone,
        "components": components,
        "entry_reason": _entry_reason(components, session, fvg_retest, order_block, bos_or_choch),
    }


def _entry_reason(components: dict, session: str, fvg_retest: dict | None, order_block: dict | None, structure_event: dict | None) -> str:
    pieces = [f"session={session}"]
    if components.get("liquidity_sweep_detected"):
        pieces.append("liquidity sweep")
    if structure_event:
        pieces.append(structure_event.get("type", "structure break"))
    if fvg_retest:
        pieces.append("FVG retest")
    if order_block:
        pieces.append("valid order block")
    return ", ".join(pieces)
