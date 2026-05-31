"""
Technical Analysis - FVG Detection
"""
import logging
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pandas as pd

import MetaTrader5 as mt5
from broker_symbols import resolve_symbol
from market.regime import classify_market_regime
from analytics.ict_diagnostics import audit_detectors
from strategy.ict import analyze_ict

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FVG_DEBUG_PATH = DATA_DIR / "fvg_debug.jsonl"
SIGNAL_AUDIT_PATH = DATA_DIR / "signal_quality_audit.jsonl"
_EMITTED_SIGNAL_FINGERPRINTS = set()
_ACTIVE_BROKER = None


def _broker_type(broker=None):
    broker = broker or _ACTIVE_BROKER
    return str(getattr(broker, "broker_type", "") or "").lower()


def _symbol_info(symbol, broker=None):
    broker = broker or _ACTIVE_BROKER
    if broker is not None and hasattr(broker, "get_symbol_info"):
        return broker.get_symbol_info(symbol)
    return mt5.symbol_info(symbol)


def _symbol_tick(symbol, broker=None):
    broker = broker or _ACTIVE_BROKER
    if broker is not None and hasattr(broker, "get_symbol_tick"):
        return broker.get_symbol_tick(symbol)
    return mt5.symbol_info_tick(symbol)


def _json_safe(value):
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


def _append_jsonl(path: Path, payload: dict):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_json_safe(payload), separators=(",", ":"), default=str) + "\n")
    except Exception as exc:
        logger.debug(f"Audit log write failed for {path}: {exc}")


def _timeframe_label(timeframe):
    mapping = {
        getattr(mt5, "TIMEFRAME_M1", 1): "M1",
        getattr(mt5, "TIMEFRAME_M5", 5): "M5",
        getattr(mt5, "TIMEFRAME_M15", 15): "M15",
        getattr(mt5, "TIMEFRAME_M30", 30): "M30",
        getattr(mt5, "TIMEFRAME_H1", 60): "H1",
        getattr(mt5, "TIMEFRAME_H4", 240): "H4",
    }
    return mapping.get(timeframe or mt5.TIMEFRAME_M5, str(timeframe or mt5.TIMEFRAME_M5))


def _signal_fingerprint(symbol, action, setup_type, timeframe, candle_timestamp):
    return "|".join([
        str(symbol or "").upper(),
        str(action or "").upper(),
        str(setup_type or "unknown"),
        _timeframe_label(timeframe),
        str(candle_timestamp or ""),
    ])


def reset_signal_fingerprints():
    """Clear in-process duplicate tracking, useful for isolated backtest scenarios."""
    _EMITTED_SIGNAL_FINGERPRINTS.clear()


def detect_fvg(symbol, timeframe=None):
    """Detect Fair Value Gaps using Trade Bible Rules."""
    debug = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "timeframe": _timeframe_label(timeframe),
        "event": "FVG_AUDIT",
        "checks": [],
        "accepted": False,
        "rejection_reason": None,
    }
    try:
        df = _fetch_rates(symbol, timeframe, 5, start_pos=1)
        if df is None or len(df) < 3:
            debug["rejection_reason"] = f"Not enough candles: {0 if df is None else len(df)}"
            _append_jsonl(FVG_DEBUG_PATH, debug)
            return None

        debug["candles"] = [
            {
                "index": int(i),
                "time": int(row.get("time", 0)),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "visual": f"O={float(row['open']):.5f} H={float(row['high']):.5f} L={float(row['low']):.5f} C={float(row['close']):.5f}",
            }
            for i, row in df.iterrows()
        ]

        latest_close = float(df.iloc[0]["close"])
        high_1 = float(df.iloc[0]["high"])
        low_1 = float(df.iloc[0]["low"])
        low_2 = float(df.iloc[2]["low"])
        high_2 = float(df.iloc[2]["high"])
        blast_low = float(df.iloc[1]["low"])
        blast_high = float(df.iloc[1]["high"])

        # Determine 50 EMA on M30 for additional signal context
        ema50 = None
        try:
            df30 = _fetch_rates(symbol, mt5.TIMEFRAME_M30, 100)
            if df30 is not None and len(df30) >= 55:
                ema50 = df30["close"].ewm(span=50, adjust=False).mean().iloc[-1]
        except Exception:
            pass

        # Estimate typical bar range (used to estimate time to reach TP)
        info = _symbol_info(symbol)
        digits = getattr(info, "digits", None) if info else None
        avg_range = (df["high"] - df["low"]).mean() if not df.empty else 0
        pip_size = 0.0001 if digits and digits > 3 else 0.01
        avg_pips = avg_range / pip_size if pip_size else None

        # Determine timeframe in minutes (default to M5)
        timeframe_minutes = 5
        if timeframe == mt5.TIMEFRAME_M1:
            timeframe_minutes = 1
        elif timeframe == mt5.TIMEFRAME_M5:
            timeframe_minutes = 5
        elif timeframe == mt5.TIMEFRAME_M15:
            timeframe_minutes = 15
        elif timeframe == mt5.TIMEFRAME_H1:
            timeframe_minutes = 60
        elif timeframe == mt5.TIMEFRAME_H4:
            timeframe_minutes = 240

        # Helper for trend labeling
        def trend_label(close, ema):
            if ema is None:
                return ""
            diff = close - ema
            pct = abs(diff) / ema if ema != 0 else 0
            if pct < 0.0002:
                return "Sideways"
            if diff > 0:
                return "Trend Continuation"
            return "Trend Reversal"

        # Use the standard Trade Bible FVG calculation based on last 4 bars:
        # Bar[2] is the FVG base bar, Bar[1] is the pivot bar, Bar[0] is the sweep bar.
        gap_threshold = pip_size * 0.5 if pip_size else 0.00005

        # Get recent swing high/low for dynamic SL/TP
        recent_high = max(df["high"].iloc[:3])  # High from last 3 bars
        recent_low = min(df["low"].iloc[:3])    # Low from last 3 bars
        volatility_buffer = avg_range * 0.1  # 10% of average range as breathing room

        logger.debug(f"FVG detection for {symbol}: low_2={low_2}, high_1={high_1}, gap={low_2-high_1}, threshold={gap_threshold}")
        bullish_gap = low_2 - high_1
        debug["checks"].append({
            "direction": "Bullish",
            "base_index": 2,
            "pivot_index": 1,
            "retest_index": 0,
            "gap_size": bullish_gap,
            "threshold": gap_threshold,
            "accepted": bool(low_2 > high_1 and bullish_gap >= gap_threshold),
            "rejection_reason": None if low_2 > high_1 and bullish_gap >= gap_threshold else (
                "No bullish gap" if low_2 <= high_1 else f"Gap too small ({bullish_gap:.5f} < {gap_threshold:.5f})"
            ),
        })
        if low_2 > high_1 and (low_2 - high_1) >= gap_threshold:
            gap_size = low_2 - high_1
            gap_zone = (high_1, low_2)
            entry = high_1
            # Dynamic SL: Use recent low minus buffer for breathing room
            sl = recent_low - volatility_buffer
            # Ensure SL is below entry for BUY
            if sl >= entry:
                sl = entry - (gap_size * 0.5)  # Fallback to gap-based
            # TP: Entry plus 2x gap plus buffer
            tp = entry + (gap_size * 2) + volatility_buffer

            if latest_close < entry:
                label = "Pullback"
            elif latest_close < low_2:
                label = "Retest"
            else:
                label = "Breakout"

            nature = f"Bullish {label}"
            tl = trend_label(latest_close, ema50)
            if tl:
                nature += f" ({tl})"

            target_pips = abs(tp - entry) / pip_size if pip_size else None
            estimated_bars = (target_pips / avg_pips) if (avg_pips and avg_pips > 0 and target_pips) else None
            estimated_minutes = round(estimated_bars * timeframe_minutes) if estimated_bars else None

            result = {
                "type": "BULLISH",
                "action": "BUY",
                "entry": float(entry),
                "sl": float(sl),
                "tp": float(tp),
                "gap_zone": gap_zone,
                "nature": nature,
                "context": f"Liquidity Void {gap_size:.5f}",
                "estimated_time_minutes": estimated_minutes,
                "estimated_bars": estimated_bars,
                "candle_timestamp": int(df.iloc[0].get("time", 0)),
                "fvg_debug": {"gap_size": float(gap_size), "threshold": float(gap_threshold), "candle_indexes": [2, 1, 0]},
            }
            debug["accepted"] = True
            debug["accepted_direction"] = "Bullish"
            debug["gap_size"] = float(gap_size)
            _append_jsonl(FVG_DEBUG_PATH, debug)
            return result

        logger.debug(f"FVG detection for {symbol}: high_2={high_2}, low_1={low_1}, gap={low_1-high_2}, threshold={gap_threshold}")
        bearish_gap = low_1 - high_2
        debug["checks"].append({
            "direction": "Bearish",
            "base_index": 2,
            "pivot_index": 1,
            "retest_index": 0,
            "gap_size": bearish_gap,
            "threshold": gap_threshold,
            "accepted": bool(high_2 < low_1 and bearish_gap >= gap_threshold),
            "rejection_reason": None if high_2 < low_1 and bearish_gap >= gap_threshold else (
                "No bearish gap" if high_2 >= low_1 else f"Gap too small ({bearish_gap:.5f} < {gap_threshold:.5f})"
            ),
        })
        if high_2 < low_1 and (low_1 - high_2) >= gap_threshold:
            gap_size = low_1 - high_2
            gap_zone = (high_2, low_1)
            entry = low_1
            # Dynamic SL: Use recent high plus buffer for breathing room
            sl = recent_high + volatility_buffer
            # Ensure SL is above entry for SELL
            if sl <= entry:
                sl = entry + (gap_size * 0.5)  # Fallback to gap-based
            # TP: Entry minus 2x gap minus buffer
            tp = entry - (gap_size * 2) - volatility_buffer

            if latest_close > entry:
                label = "Pullback"
            elif latest_close > high_2:
                label = "Retest"
            else:
                label = "Breakout"

            nature = f"Bearish {label}"
            tl = trend_label(latest_close, ema50)
            if tl:
                nature += f" ({tl})"

            target_pips = abs(tp - entry) / pip_size if pip_size else None
            estimated_bars = (target_pips / avg_pips) if (avg_pips and avg_pips > 0 and target_pips) else None
            estimated_minutes = round(estimated_bars * timeframe_minutes) if estimated_bars else None

            result = {
                "type": "BEARISH",
                "action": "SELL",
                "entry": float(entry),
                "sl": float(sl),
                "tp": float(tp),
                "gap_zone": gap_zone,
                "nature": nature,
                "context": f"Liquidity Void {gap_size:.5f}",
                "estimated_time_minutes": estimated_minutes,
                "estimated_bars": estimated_bars,
                "candle_timestamp": int(df.iloc[0].get("time", 0)),
                "fvg_debug": {"gap_size": float(gap_size), "threshold": float(gap_threshold), "candle_indexes": [2, 1, 0]},
            }
            debug["accepted"] = True
            debug["accepted_direction"] = "Bearish"
            debug["gap_size"] = float(gap_size)
            _append_jsonl(FVG_DEBUG_PATH, debug)
            return result

        debug["rejection_reason"] = "; ".join(
            item["rejection_reason"] for item in debug["checks"] if item.get("rejection_reason")
        ) or "No valid FVG"
        _append_jsonl(FVG_DEBUG_PATH, debug)
        return None
    except Exception as e:
        debug["rejection_reason"] = f"FVG detection error: {e}"
        _append_jsonl(FVG_DEBUG_PATH, debug)
        logger.error(f"FVG detection error for {symbol}: {e}")
        return None


def _fetch_rates(symbol, timeframe=None, count=60, start_pos=0):
    timeframe = timeframe or mt5.TIMEFRAME_M5
    broker = _ACTIVE_BROKER
    if broker is not None and hasattr(broker, "copy_rates_from_pos"):
        rates = broker.copy_rates_from_pos(symbol, timeframe, start_pos, count)
    else:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, start_pos, count)
    if rates is None or len(rates) == 0:
        return None
    return pd.DataFrame(rates)


def _pip_size_for_symbol(symbol, info=None):
    digits = getattr(info, "digits", None) if info else None
    if digits is None:
        return 0.01 if symbol and symbol.endswith("JPY") else 0.0001
    return 0.0001 if digits > 3 else 0.01


def _action_from_direction(direction):
    if direction == "Bullish":
        return "BUY"
    if direction == "Bearish":
        return "SELL"
    return None


def _zone_overlap(zone_a, zone_b):
    if not zone_a or not zone_b:
        return False
    a_low, a_high = sorted([float(zone_a[0]), float(zone_a[1])])
    b_low, b_high = sorted([float(zone_b[0]), float(zone_b[1])])
    return max(a_low, b_low) <= min(a_high, b_high)


def build_chart_visuals(symbol, timeframe=None, count=80):
    """Build lightweight chart overlays: recent closes, swing trendline, support, resistance."""
    df = _fetch_rates(symbol, timeframe, count)
    if df is None or len(df) < 12:
        return {"symbol": symbol, "candles": [], "trendline": None, "levels": []}

    df = df.sort_values("time").reset_index(drop=True)
    closes = [
        {"time": int(row["time"]), "value": float(row["close"])}
        for _, row in df.iterrows()
    ]

    recent = df.tail(60).reset_index(drop=True)
    swing_highs = []
    swing_lows = []
    for i in range(2, len(recent) - 2):
        high = float(recent.loc[i, "high"])
        low = float(recent.loc[i, "low"])
        if high >= float(recent.loc[i - 2:i + 2, "high"].max()):
            swing_highs.append({"time": int(recent.loc[i, "time"]), "value": high})
        if low <= float(recent.loc[i - 2:i + 2, "low"].min()):
            swing_lows.append({"time": int(recent.loc[i, "time"]), "value": low})

    first_close = float(recent.iloc[0]["close"])
    last_close = float(recent.iloc[-1]["close"])
    bullish = last_close >= first_close
    swings = swing_lows if bullish else swing_highs
    trendline = None
    if len(swings) >= 2:
        trendline = {
            "type": "support" if bullish else "resistance",
            "points": [swings[-2], swings[-1]],
        }

    levels = [
        {
            "label": "Resistance",
            "value": float(recent["high"].tail(30).max()),
            "color": "#f59e0b",
        },
        {
            "label": "Support",
            "value": float(recent["low"].tail(30).min()),
            "color": "#22c55e",
        },
    ]

    return {
        "symbol": symbol,
        "candles": closes,
        "trendline": trendline,
        "levels": levels,
        "bias": "Bullish" if bullish else "Bearish",
        "last_price": last_close,
    }


def detect_order_block(symbol, timeframe=None):
    df = _fetch_rates(symbol, timeframe, 12)
    if df is None or len(df) < 8:
        return None

    latest = df.iloc[0]
    prior = df.iloc[1:6]
    prior_high = float(prior["high"].max())
    prior_low = float(prior["low"].min())
    latest_close = float(latest["close"])
    latest_open = float(latest["open"])

    if latest_close > prior_high and latest_close > latest_open:
        return {
            "type": "BUY",
            "zone": (prior_high, prior_low),
            "strength": "Bullish",
            "description": "Order block breakout above local supply",
        }
    if latest_close < prior_low and latest_close < latest_open:
        return {
            "type": "SELL",
            "zone": (prior_low, prior_high),
            "strength": "Bearish",
            "description": "Order block breakdown below local demand",
        }
    return None


def detect_liquidity_sweep(symbol, timeframe=None):
    """Detect stop-run sweeps above/below recent highs/lows followed by rejection."""
    df = _fetch_rates(symbol, timeframe, 30)
    if df is None or len(df) < 12:
        return None

    latest = df.iloc[0]
    prior = df.iloc[1:20]
    prior_high = float(prior["high"].max())
    prior_low = float(prior["low"].min())
    latest_high = float(latest["high"])
    latest_low = float(latest["low"])
    latest_close = float(latest["close"])

    range_size = prior_high - prior_low
    if range_size <= 0:
        return None
    tolerance = range_size * 0.03

    if latest_low < prior_low - tolerance and latest_close > prior_low:
        return {
            "direction": "Bullish",
            "type": "Sell-side liquidity sweep",
            "level": prior_low,
            "swept_price": latest_low,
            "description": "Price swept sell-side liquidity and reclaimed the range",
        }
    if latest_high > prior_high + tolerance and latest_close < prior_high:
        return {
            "direction": "Bearish",
            "type": "Buy-side liquidity sweep",
            "level": prior_high,
            "swept_price": latest_high,
            "description": "Price swept buy-side liquidity and rejected back into the range",
        }
    return None


def detect_market_structure_shift(symbol, timeframe=None):
    """Detect a small break of local structure for earlier confirmation."""
    df = _fetch_rates(symbol, timeframe, 18)
    if df is None or len(df) < 10:
        return None

    latest_close = float(df.iloc[0]["close"])
    prior = df.iloc[1:8]
    prior_high = float(prior["high"].max())
    prior_low = float(prior["low"].min())

    if latest_close > prior_high:
        return {
            "direction": "Bullish",
            "type": "MSS/BOS",
            "break_level": prior_high,
            "description": "Close broke above local structure",
        }
    if latest_close < prior_low:
        return {
            "direction": "Bearish",
            "type": "MSS/BOS",
            "break_level": prior_low,
            "description": "Close broke below local structure",
        }
    return None


def detect_higher_timeframe_bias(symbol):
    """Use H1 EMA alignment as directional bias."""
    df = _fetch_rates(symbol, mt5.TIMEFRAME_H1, 80)
    if df is None or len(df) < 55:
        return {"direction": "Neutral", "score": 0.0, "description": "Not enough H1 data"}

    forward = df.iloc[::-1].reset_index(drop=True)
    close = forward["close"].astype(float)
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    last = float(close.iloc[-1])

    if last > ema20 > ema50:
        return {"direction": "Bullish", "score": 1.0, "ema20": float(ema20), "ema50": float(ema50), "description": "H1 price above aligned EMAs"}
    if last < ema20 < ema50:
        return {"direction": "Bearish", "score": 1.0, "ema20": float(ema20), "ema50": float(ema50), "description": "H1 price below aligned EMAs"}
    return {"direction": "Neutral", "score": 0.35, "ema20": float(ema20), "ema50": float(ema50), "description": "H1 bias is mixed"}


def detect_session_bias():
    """Score current UTC session quality for intraday FX setups."""
    hour = datetime.now(timezone.utc).hour
    if 7 <= hour < 11:
        return {"session": "London Open", "score": 1.0, "description": "High-liquidity London window"}
    if 12 <= hour < 16:
        return {"session": "New York", "score": 1.0, "description": "High-liquidity New York window"}
    if 16 <= hour < 20:
        return {"session": "NY Continuation", "score": 0.65, "description": "Moderate continuation window"}
    if 0 <= hour < 6:
        return {"session": "Asia", "score": 0.45, "description": "Range-building Asia window"}
    return {"session": "Transition", "score": 0.35, "description": "Lower-quality transition window"}


def detect_displacement(symbol, timeframe=None):
    """Detect strong candle body expansion versus recent average body size."""
    df = _fetch_rates(symbol, timeframe, 24)
    if df is None or len(df) < 12:
        return None

    latest = df.iloc[0]
    prior = df.iloc[1:12]
    high = float(latest["high"])
    low = float(latest["low"])
    open_price = float(latest["open"])
    close = float(latest["close"])
    body = abs(float(latest["close"]) - float(latest["open"]))
    candle_range = max(high - low, 1e-12)
    avg_body = (prior["close"].astype(float) - prior["open"].astype(float)).abs().mean()
    avg_range = max(float((prior["high"].astype(float) - prior["low"].astype(float)).mean()), 1e-12)
    if avg_body <= 0:
        return None

    ratio = body / avg_body
    body_to_range = body / candle_range
    range_ratio = candle_range / avg_range
    close_position = (close - low) / candle_range
    direction = "Bullish" if close > open_price else "Bearish"
    close_quality = close_position if direction == "Bullish" else 1.0 - close_position
    fake = ratio >= 1.4 and body_to_range < 0.45
    return {
        "direction": direction,
        "ratio": round(float(ratio), 2),
        "body_ratio": round(float(ratio), 2),
        "body_to_range": round(float(body_to_range), 2),
        "range_ratio": round(float(range_ratio), 2),
        "close_quality": round(float(close_quality), 2),
        "fake": bool(fake),
        "score": min(1.0, float(ratio) / 2.0) * min(1.0, body_to_range / 0.65),
        "description": f"{direction} displacement candle {ratio:.2f}x average body",
    }


def detect_market_quality(symbol, timeframe=None):
    """Detect chop, directional momentum, volatility quality, and candle close quality."""
    df = _fetch_rates(symbol, timeframe, 60)
    if df is None or len(df) < 30:
        return {
            "safe": False,
            "score": 0.0,
            "chop": True,
            "momentum_score": 0.0,
            "volatility_quality": 0.0,
            "close_quality": 0.0,
            "description": "Not enough data for market quality check",
        }

    ordered = df.iloc[::-1].reset_index(drop=True)
    close = ordered["close"].astype(float)
    high = ordered["high"].astype(float)
    low = ordered["low"].astype(float)
    open_price = ordered["open"].astype(float)
    latest_high = float(high.iloc[-1])
    latest_low = float(low.iloc[-1])
    latest_open = float(open_price.iloc[-1])
    latest_close = float(close.iloc[-1])
    latest_range = max(latest_high - latest_low, 1e-12)
    latest_body = abs(latest_close - latest_open)
    close_pos = (latest_close - latest_low) / latest_range
    close_quality = close_pos if latest_close >= latest_open else 1.0 - close_pos

    ema10 = close.ewm(span=10, adjust=False).mean()
    ema30 = close.ewm(span=30, adjust=False).mean()
    ema_gap = abs(float(ema10.iloc[-1] - ema30.iloc[-1]))
    avg_range = max(float((high - low).tail(20).mean()), 1e-12)
    momentum_score = min(1.0, ema_gap / avg_range)
    recent_range = max(float(high.tail(20).max() - low.tail(20).min()), 1e-12)
    directional_move = abs(float(close.iloc[-1] - close.iloc[-10]))
    directional_efficiency = min(1.0, directional_move / recent_range)
    volatility_ratio = latest_range / avg_range
    volatility_quality = 1.0 - min(1.0, abs(volatility_ratio - 1.25) / 1.25)
    wick_ratio = 1.0 - (latest_body / latest_range)
    chop = directional_efficiency < 0.22 and momentum_score < 0.18
    low_momentum = momentum_score < 0.16
    fake_displacement = volatility_ratio >= 1.4 and latest_body / latest_range < 0.45
    noisy = wick_ratio > 0.72 and close_quality < 0.62
    safe = not (chop or low_momentum or fake_displacement or noisy)
    score = (
        directional_efficiency * 0.35
        + momentum_score * 0.25
        + volatility_quality * 0.20
        + close_quality * 0.20
    )

    blockers = []
    if chop:
        blockers.append("ranging/chop")
    if low_momentum:
        blockers.append("low directional momentum")
    if fake_displacement:
        blockers.append("fake displacement")
    if noisy:
        blockers.append("noisy candle close")

    return {
        "safe": bool(safe),
        "score": round(float(score), 3),
        "chop": bool(chop),
        "low_momentum": bool(low_momentum),
        "fake_displacement": bool(fake_displacement),
        "noisy": bool(noisy),
        "momentum_score": round(float(momentum_score), 3),
        "directional_efficiency": round(float(directional_efficiency), 3),
        "volatility_ratio": round(float(volatility_ratio), 2),
        "volatility_quality": round(float(volatility_quality), 3),
        "close_quality": round(float(close_quality), 3),
        "body_to_range": round(float(latest_body / latest_range), 3),
        "description": "Clean directional conditions" if safe else "Blocked: " + ", ".join(blockers),
    }


def detect_premium_discount(symbol, action, timeframe=None):
    """Classify whether current price is in premium or discount of recent dealing range."""
    df = _fetch_rates(symbol, timeframe, 50)
    if df is None or len(df) < 20:
        return None

    recent_high = float(df["high"].max())
    recent_low = float(df["low"].min())
    current = float(df.iloc[0]["close"])
    midpoint = (recent_high + recent_low) / 2
    zone = "Discount" if current <= midpoint else "Premium"
    aligned = (action == "BUY" and zone == "Discount") or (action == "SELL" and zone == "Premium")
    return {
        "zone": zone,
        "aligned": aligned,
        "midpoint": midpoint,
        "range_high": recent_high,
        "range_low": recent_low,
        "description": f"Price is in {zone.lower()} relative to recent dealing range",
    }


def detect_spread_safety(symbol):
    """Check spread quality before treating a setup as clean."""
    try:
        info = _symbol_info(symbol)
        tick = _symbol_tick(symbol)
        if tick is None:
            return {"safe": False, "spread_pips": None, "description": "No tick data"}
        pip_size = _pip_size_for_symbol(symbol, info)
        spread_pips = abs(float(tick.ask) - float(tick.bid)) / pip_size if pip_size else 999
        max_spread = 35 if symbol and ("XAU" in symbol or "GOLD" in symbol) else 2.5
        return {
            "safe": spread_pips <= max_spread,
            "spread_pips": round(spread_pips, 2),
            "max_spread_pips": max_spread,
            "description": f"Spread {spread_pips:.2f} pips",
        }
    except Exception as e:
        return {"safe": False, "spread_pips": None, "description": f"Spread check failed: {e}"}


def detect_false_move(symbol, timeframe=None, action=None):
    """Classify failed breakouts, stop-hunts, and clean breakouts."""
    df = _fetch_rates(symbol, timeframe, 36)
    if df is None or len(df) < 16:
        return {"type": "UNKNOWN", "safe": True, "score": 0.0, "description": "Not enough data for false-move check"}

    ordered = df.sort_values("time").reset_index(drop=True)
    latest = ordered.iloc[-1]
    prior = ordered.iloc[-16:-1]
    prior_high = float(prior["high"].max())
    prior_low = float(prior["low"].min())
    high = float(latest["high"])
    low = float(latest["low"])
    open_price = float(latest["open"])
    close = float(latest["close"])
    body = abs(close - open_price)
    candle_range = max(high - low, 1e-12)
    wick_ratio = 1.0 - (body / candle_range)
    range_size = max(prior_high - prior_low, 1e-12)
    sweep_buffer = range_size * 0.015

    swept_high = high > prior_high + sweep_buffer
    swept_low = low < prior_low - sweep_buffer
    closed_above = close > prior_high
    closed_below = close < prior_low
    bullish_reclaim = swept_low and close > prior_low
    bearish_reject = swept_high and close < prior_high

    if bullish_reclaim:
        aligned = action in [None, "BUY"]
        return {
            "type": "LIQUIDITY_SWEEP_REVERSAL",
            "direction": "Bullish",
            "safe": bool(aligned),
            "score": 0.85 if wick_ratio >= 0.45 else 0.72,
            "level": prior_low,
            "swept_price": low,
            "wick_ratio": round(wick_ratio, 2),
            "description": "Sell-side liquidity was swept and price reclaimed the range",
        }
    if bearish_reject:
        aligned = action in [None, "SELL"]
        return {
            "type": "LIQUIDITY_SWEEP_REVERSAL",
            "direction": "Bearish",
            "safe": bool(aligned),
            "score": 0.85 if wick_ratio >= 0.45 else 0.72,
            "level": prior_high,
            "swept_price": high,
            "wick_ratio": round(wick_ratio, 2),
            "description": "Buy-side liquidity was swept and price rejected back into the range",
        }
    if swept_high and not closed_above:
        return {
            "type": "FAILED_BREAKOUT",
            "direction": "Bearish",
            "safe": action in [None, "SELL"],
            "score": 0.68,
            "level": prior_high,
            "swept_price": high,
            "wick_ratio": round(wick_ratio, 2),
            "description": "Upside breakout failed to close beyond resistance",
        }
    if swept_low and not closed_below:
        return {
            "type": "FAILED_BREAKOUT",
            "direction": "Bullish",
            "safe": action in [None, "BUY"],
            "score": 0.68,
            "level": prior_low,
            "swept_price": low,
            "wick_ratio": round(wick_ratio, 2),
            "description": "Downside breakout failed to close beyond support",
        }
    if closed_above or closed_below:
        direction = "Bullish" if closed_above else "Bearish"
        aligned = action == _action_from_direction(direction)
        follow_through = body / candle_range >= 0.55
        return {
            "type": "REAL_BREAKOUT" if follow_through else "BREAKOUT_UNCONFIRMED",
            "direction": direction,
            "safe": bool(aligned and follow_through),
            "score": 0.62 if follow_through else 0.35,
            "level": prior_high if closed_above else prior_low,
            "wick_ratio": round(wick_ratio, 2),
            "description": "Breakout closed beyond structure" if follow_through else "Breakout close lacks clean body follow-through",
        }

    return {
        "type": "RANGE",
        "direction": "Neutral",
        "safe": True,
        "score": 0.25,
        "wick_ratio": round(wick_ratio, 2),
        "description": "No obvious false breakout or stop-hunt",
    }


def detect_news_move(symbol, timeframe=None):
    """Detect event-like volatility spikes without relying on an external news calendar."""
    df = _fetch_rates(symbol, timeframe, 36)
    spread = detect_spread_safety(symbol)
    if df is None or len(df) < 18:
        return {
            "mode": "WATCH",
            "safe": False,
            "plan": "WAIT",
            "description": "Not enough data for news-spike check",
            "spread": spread,
        }

    ordered = df.sort_values("time").reset_index(drop=True)
    latest = ordered.iloc[-1]
    prior = ordered.iloc[-18:-1]
    open_price = float(latest["open"])
    close = float(latest["close"])
    high = float(latest["high"])
    low = float(latest["low"])
    candle_range = max(high - low, 1e-12)
    body = abs(close - open_price)
    avg_range = max(float((prior["high"].astype(float) - prior["low"].astype(float)).mean()), 1e-12)
    avg_body = max(float((prior["close"].astype(float) - prior["open"].astype(float)).abs().mean()), 1e-12)
    range_ratio = candle_range / avg_range
    body_ratio = body / avg_body
    direction = "Bullish" if close > open_price else "Bearish"
    impulse = range_ratio >= 2.2 or body_ratio >= 2.4
    extreme = range_ratio >= 3.5 or body_ratio >= 3.8
    spread_safe = spread.get("safe") is not False

    if extreme and not spread_safe:
        mode, safe, plan = "ACTIVE", False, "WAIT_SPREAD"
        description = "Extreme event spike with unsafe spread"
    elif extreme:
        mode, safe, plan = "ACTIVE", False, "WAIT_RETEST"
        description = "Extreme event spike detected; wait for retest or fade confirmation"
    elif impulse and spread_safe:
        mode, safe, plan = "FOLLOW_RETEST", True, "CONTINUATION_OR_FADE"
        description = "Event impulse detected with manageable spread; follow only after confirmation"
    elif impulse:
        mode, safe, plan = "WATCH", False, "WAIT_SPREAD"
        description = "Event impulse detected but spread is not clean"
    else:
        mode, safe, plan = "NORMAL", True, "NORMAL"
        description = "No active event spike"

    return {
        "mode": mode,
        "safe": safe,
        "plan": plan,
        "direction": direction,
        "range_ratio": round(range_ratio, 2),
        "body_ratio": round(body_ratio, 2),
        "news_high": high,
        "news_low": low,
        "spread": spread,
        "description": description,
    }


def build_early_entry_signal(symbol, setup, timeframe=None):
    """Create a tradable early-entry candidate when no FVG exists but setup quality is high."""
    direction = setup.get("direction")
    action = _action_from_direction(direction)
    if action not in ["BUY", "SELL"]:
        return None

    df = _fetch_rates(symbol, timeframe, 30)
    if df is None or len(df) < 10:
        return None

    info = _symbol_info(symbol)
    pip_size = _pip_size_for_symbol(symbol, info)
    entry = float(df.iloc[0]["close"])
    avg_range = float((df["high"].astype(float) - df["low"].astype(float)).mean())
    buffer = max(avg_range * 0.2, pip_size * 3)
    recent_high = float(df["high"].iloc[:10].max())
    recent_low = float(df["low"].iloc[:10].min())

    if action == "BUY":
        sl = recent_low - buffer
        risk = max(entry - sl, pip_size * 5)
        tp = entry + (risk * 2)
        signal_type = "EARLY_BULLISH"
    else:
        sl = recent_high + buffer
        risk = max(sl - entry, pip_size * 5)
        tp = entry - (risk * 2)
        signal_type = "EARLY_BEARISH"

    return {
        "type": signal_type,
        "action": action,
        "entry": float(entry),
        "sl": float(sl),
        "tp": float(tp),
        "gap_zone": None,
        "nature": f"{direction} Early Entry ({setup.get('grade', 'Setup')})",
        "context": setup.get("summary", "Composite early-entry setup"),
        "estimated_time_minutes": None,
        "estimated_bars": None,
        "early_entry": True,
    }


def detect_market_structure(symbol, timeframe=None):
    df = _fetch_rates(symbol, timeframe, 8)
    if df is None or len(df) < 5:
        return None

    highs = df["high"].iloc[:4]
    lows = df["low"].iloc[:4]

    if highs.iloc[0] > highs.iloc[1] > highs.iloc[2] and lows.iloc[0] > lows.iloc[1] > lows.iloc[2]:
        return {"structure": "Bullish", "description": "Higher highs and higher lows"}
    if highs.iloc[0] < highs.iloc[1] < highs.iloc[2] and lows.iloc[0] < lows.iloc[1] < lows.iloc[2]:
        return {"structure": "Bearish", "description": "Lower highs and lower lows"}
    return None


def detect_trend_strength(symbol, timeframe=None):
    df = _fetch_rates(symbol, timeframe, 120)
    if df is None or len(df) < 30:
        return {"score": 0.0, "label": "Unknown"}

    df_forward = df.iloc[::-1].reset_index(drop=True)
    close = df_forward["close"].astype(float)
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    latest_close = float(close.iloc[-1])
    diff = latest_close - ema50
    pct = abs(diff) / ema50 if ema50 != 0 else 0
    score = min(1.0, pct * 5)
    label = "Sideways"
    if diff > 0:
        label = "Bullish Trend"
    elif diff < 0:
        label = "Bearish Trend"

    return {"score": round(score, 3), "label": label, "ema20": float(ema20), "ema50": float(ema50)}


def detect_liquidity_zone(symbol, timeframe=None):
    df = _fetch_rates(symbol, timeframe, 20)
    if df is None or len(df) < 8:
        return None

    latest = df.iloc[0]
    prior = df.iloc[1:20]
    recent_high = float(prior["high"].max())
    recent_low = float(prior["low"].min())
    latest_close = float(latest["close"])
    range_size = recent_high - recent_low
    if range_size <= 0:
        return None

    threshold = range_size * 0.15
    if abs(latest_close - recent_high) <= threshold:
        return {
            "type": "Sell Liquidity",
            "zone": (recent_high - threshold, recent_high),
            "description": "Price near recent sell-side liquidity pool",
        }
    if abs(latest_close - recent_low) <= threshold:
        return {
            "type": "Buy Liquidity",
            "zone": (recent_low, recent_low + threshold),
            "description": "Price near recent buy-side liquidity pool",
        }
    return None


def detect_divergence(symbol, timeframe=None):
    df = _fetch_rates(symbol, timeframe, 24)
    if df is None or len(df) < 16:
        return None

    df_forward = df.iloc[::-1].reset_index(drop=True)
    close = df_forward["close"].astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss.replace({0: 1e-9})
    rsi = 100 - (100 / (1 + rs))
    if rsi.isnull().all():
        return None

    last_rsi = float(rsi.iloc[-1])
    prior_rsi = float(rsi.iloc[-4]) if len(rsi) >= 4 else last_rsi
    last_close = float(close.iloc[-1])
    prior_close = float(close.iloc[-4]) if len(close) >= 4 else last_close

    if last_close > prior_close and last_rsi < prior_rsi:
        return {"type": "Bearish", "label": "Bearish Divergence", "rsi_change": last_rsi - prior_rsi}
    if last_close < prior_close and last_rsi > prior_rsi:
        return {"type": "Bullish", "label": "Bullish Divergence", "rsi_change": last_rsi - prior_rsi}
    return None


def _estimate_confluence(signal):
    score = 0.0
    setup = signal.get("setup_score") or {}
    score += min(0.30, float(setup.get("score", 0.0)) * 0.30)
    if signal.get("order_block"):
        score += 0.20
    if signal.get("structure_break"):
        structure = signal["structure_break"].get("structure", "")
        action = signal.get("action", "")
        if (structure == "Bullish" and action == "BUY") or (structure == "Bearish" and action == "SELL"):
            score += 0.20
    trend = signal.get("trend_strength") or {}
    score += min(0.25, float(trend.get("score", 0.0)) * 0.25)
    if signal.get("liquidity_zone"):
        score += 0.15
    divergence = signal.get("divergence")
    if divergence and divergence.get("type") in ["Bullish", "Bearish"]:
        action = signal.get("action")
        if (divergence["type"] == "Bullish" and action == "BUY") or (divergence["type"] == "Bearish" and action == "SELL"):
            score += 0.15
    return round(min(score, 1.0), 3)


def score_composite_setup(symbol, signal=None, timeframe=None):
    """Score non-FVG and FVG confluence for cleaner early entries."""
    action = signal.get("action") if signal else None
    fvg_zone = signal.get("gap_zone") if signal else None

    sweep = detect_liquidity_sweep(symbol, timeframe)
    mss = detect_market_structure_shift(symbol, timeframe)
    order_block = detect_order_block(symbol, timeframe)
    htf_bias = detect_higher_timeframe_bias(symbol)
    session = detect_session_bias()
    displacement = detect_displacement(symbol, timeframe)

    directions = [
        item.get("direction")
        for item in [sweep, mss, displacement, htf_bias]
        if item and item.get("direction") in ["Bullish", "Bearish"]
    ]
    if not action and directions:
        bullish = directions.count("Bullish")
        bearish = directions.count("Bearish")
        action = "BUY" if bullish >= bearish else "SELL"

    premium_discount = detect_premium_discount(symbol, action, timeframe) if action else None
    spread = detect_spread_safety(symbol)
    false_move = detect_false_move(symbol, timeframe, action)
    news_move = detect_news_move(symbol, timeframe)
    market_quality = detect_market_quality(symbol, timeframe)
    regime_df = _fetch_rates(symbol, timeframe, 80)
    market_regime = classify_market_regime(symbol, regime_df, spread=spread, news_move=news_move)
    ob_aligned = False
    ob_fvg_overlap = False
    if order_block and action:
        ob_aligned = order_block.get("type") == action
        ob_fvg_overlap = _zone_overlap(order_block.get("zone"), fvg_zone)

    def component(key, label, passed, points, max_points, detail):
        return {
            "key": key,
            "label": label,
            "passed": bool(passed),
            "points": round(points if passed else 0, 2),
            "max_points": max_points,
            "detail": detail,
        }

    sweep_aligned = sweep and action == _action_from_direction(sweep.get("direction"))
    mss_aligned = mss and action == _action_from_direction(mss.get("direction"))
    htf_aligned = htf_bias and action == _action_from_direction(htf_bias.get("direction"))
    displacement_aligned = displacement and action == _action_from_direction(displacement.get("direction"))

    components = [
        component("liquidity_sweep", "Liquidity Sweep", sweep_aligned, 25, 25, sweep.get("description") if sweep else "No clean sweep"),
        component("mss", "MSS/BOS", mss_aligned, 20, 20, mss.get("description") if mss else "No local structure shift"),
        component(
            "ob_fvg",
            "OB/FVG Zone",
            bool(ob_aligned and (ob_fvg_overlap or not fvg_zone)),
            15,
            15,
            "Order block aligns with setup" + (" and overlaps FVG" if ob_fvg_overlap else "") if ob_aligned else "No aligned order block",
        ),
        component("htf_bias", "HTF Bias", htf_aligned, 15, 15, htf_bias.get("description") if htf_bias else "No HTF bias"),
        component("session", "Session", session.get("score", 0) >= 0.65, 10, 10, session.get("description")),
        component("displacement", "Displacement", displacement_aligned and displacement.get("score", 0) >= 0.6, 10, 10, displacement.get("description") if displacement else "No displacement"),
        component("premium_discount", "Premium/Discount", premium_discount and premium_discount.get("aligned"), 10, 10, premium_discount.get("description") if premium_discount else "No dealing range"),
        component("false_move", "False Move", false_move.get("type") in ["LIQUIDITY_SWEEP_REVERSAL", "FAILED_BREAKOUT"] and false_move.get("safe"), 10, 10, false_move.get("description")),
        component("news_safety", "News Safety", news_move.get("safe") and news_move.get("mode") != "ACTIVE", 10, 10, news_move.get("description")),
        component("spread", "Spread Safe", spread.get("safe"), 5, 5, spread.get("description")),
    ]

    points = sum(c["points"] for c in components)
    max_points = sum(c["max_points"] for c in components)
    raw_score = points / max_points if max_points else 0.0
    score = raw_score
    component_map = {c["key"]: c for c in components}

    archetypes = []

    def add_archetype(key, label, passed, boost, detail):
        archetypes.append({
            "key": key,
            "label": label,
            "passed": bool(passed),
            "boost": boost if passed else 0.0,
            "detail": detail,
        })

    add_archetype(
        "sweep_reversal",
        "Sweep Reversal",
        sweep_aligned and (displacement_aligned or premium_discount and premium_discount.get("aligned")),
        0.72,
        "Liquidity sweep plus rejection/displacement or premium-discount alignment",
    )
    add_archetype(
        "structure_continuation",
        "Structure Continuation",
        mss_aligned and (htf_aligned or displacement_aligned),
        0.70,
        "MSS/BOS aligned with higher-timeframe bias or displacement",
    )
    add_archetype(
        "order_block_mitigation",
        "Order Block Mitigation",
        ob_aligned and (premium_discount and premium_discount.get("aligned") or htf_aligned),
        0.68,
        "Aligned order block with value-zone or higher-timeframe support",
    )
    add_archetype(
        "fvg_momentum",
        "FVG Momentum",
        bool(signal) and displacement_aligned and (htf_aligned or premium_discount and premium_discount.get("aligned")),
        0.62,
        "FVG with displacement and one directional context filter",
    )
    add_archetype(
        "scalp_retest",
        "Scalp Retest",
        bool(signal) and (component_map["spread"].get("passed")) and (sweep_aligned or mss_aligned or displacement_aligned),
        0.58,
        "Fast retest with spread safe and at least one structural trigger",
    )
    add_archetype(
        "false_move_reversal",
        "False Move Reversal",
        false_move.get("type") in ["LIQUIDITY_SWEEP_REVERSAL", "FAILED_BREAKOUT"] and false_move.get("safe") and (sweep_aligned or displacement_aligned),
        0.74,
        "Failed breakout or stop-hunt aligned with reversal structure",
    )
    add_archetype(
        "post_news_retest",
        "Post-News Retest",
        news_move.get("mode") == "FOLLOW_RETEST" and spread.get("safe") and (mss_aligned or displacement_aligned),
        0.66,
        "News impulse is tradable only after spread-safe confirmation",
    )

    passed_archetypes = [a for a in archetypes if a["passed"]]
    archetype_score = max([a["boost"] for a in passed_archetypes], default=0.0)
    archetype_boosts_enabled = os.getenv("SCORE_ARCHETYPE_BOOSTS", "false").lower() in ["true", "1", "yes"]
    if archetype_boosts_enabled:
        score = max(score, archetype_score)

    if score >= 0.78:
        grade = "A"
    elif score >= 0.65:
        grade = "B"
    elif score >= 0.50:
        grade = "C"
    else:
        grade = "D"

    direction = "Bullish" if action == "BUY" else "Bearish" if action == "SELL" else "Neutral"
    passed_labels = [c["label"] for c in components if c["passed"]]
    summary = ", ".join(passed_labels[:4]) if passed_labels else "No high-quality early-entry confluence"

    def bucket_score(*keys):
        selected = [component_map[key] for key in keys if key in component_map]
        max_value = sum(float(item.get("max_points") or 0) for item in selected)
        value = sum(float(item.get("points") or 0) for item in selected)
        return round(value / max_value, 3) if max_value else 0.0

    score_breakdown = {
        "trend_score": bucket_score("htf_bias", "premium_discount"),
        "structure_score": bucket_score("mss", "displacement"),
        "fvg_score": bucket_score("ob_fvg"),
        "liquidity_score": bucket_score("liquidity_sweep", "false_move"),
        "volume_score": round(float((market_quality or {}).get("volatility_quality") or 0.0), 3),
        "session_score": round(float((session or {}).get("score") or 0.0), 3),
        "execution_score": bucket_score("spread", "news_safety"),
    }
    audit_payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "action": action,
        "timeframe": _timeframe_label(timeframe),
        "score": round(score, 3),
        "raw_score": round(raw_score, 3),
        "archetype_score": round(archetype_score, 3),
        "archetype_boosts_enabled": archetype_boosts_enabled,
        "score_breakdown": score_breakdown,
        "components": components,
        "archetypes": archetypes,
    }
    _append_jsonl(SIGNAL_AUDIT_PATH, audit_payload)

    return {
        "score": round(score, 3),
        "raw_score": round(raw_score, 3),
        "archetype_score": round(archetype_score, 3),
        "archetype_boosts_enabled": archetype_boosts_enabled,
        "score_breakdown": score_breakdown,
        "points": round(points, 2),
        "max_points": max_points,
        "grade": grade,
        "archetype": passed_archetypes[0]["label"] if passed_archetypes else "Context Watch",
        "archetypes": archetypes,
        "direction": direction,
        "action": action,
        "summary": summary,
        "components": components,
        "liquidity_sweep": sweep,
        "market_structure_shift": mss,
        "higher_timeframe_bias": htf_bias,
        "session_bias": session,
        "displacement": displacement,
        "premium_discount": premium_discount,
        "false_move": false_move,
        "news_move": news_move,
        "market_quality": market_quality,
        "market_regime": market_regime,
        "spread": spread,
    }


def _normalize_scan_symbols(symbols):
    if isinstance(symbols, str):
        raw_symbols = symbols.split(",")
    else:
        raw_symbols = symbols or []
    normalized = []
    for symbol in raw_symbols:
        symbol = str(symbol or "").strip().upper()
        if symbol and symbol not in normalized:
            normalized.append(symbol)
    return normalized


def scan_symbols(symbols, timeframe=None, return_diagnostics=False, broker=None):
    """Scan symbols for FVG and high-confluence early-entry setups."""
    global _ACTIVE_BROKER
    previous_broker = _ACTIVE_BROKER
    _ACTIVE_BROKER = broker
    signals = []
    diagnostics = []
    try:
        for configured_symbol in _normalize_scan_symbols(symbols):
            resolution = resolve_symbol(configured_symbol) if _broker_type(broker) in {"", "mt5"} else None
            symbol = (resolution.resolved if resolution else None) or configured_symbol
            result = {
                "symbol": symbol,
                "configured_symbol": configured_symbol,
                "resolved_symbol": resolution.resolved if resolution else symbol,
                "mapped": resolution.mapped if resolution else False,
                "mapping_reason": resolution.reason if resolution else "Active broker symbol",
                "symbol_candidates": resolution.candidates if resolution else [symbol],
                "visible": None,
                "status": "scanning",
                "reason": "",
                "setup_score": None,
                "fvg_detected": False,
                "early_entry_detected": False,
            }
            try:
                info = (resolution.info if resolution else None) or _symbol_info(symbol, broker)
                if info is None:
                    broker_label = _broker_type(broker) or "MT5"
                    result.update({"visible": False, "status": "skipped", "reason": (resolution.reason if resolution else None) or f"Symbol not found in {broker_label}"})
                    diagnostics.append(result)
                    continue
                result["visible"] = True if _broker_type(broker) not in {"", "mt5"} else bool(resolution.visible or getattr(info, "visible", False))
                if not result["visible"]:
                    try:
                        result["visible"] = bool(mt5.symbol_select(symbol, True))
                    except Exception:
                        result["visible"] = False
                if not result["visible"]:
                    result.update({"status": "skipped", "reason": "Symbol is not visible in MT5 Market Watch"})
                    diagnostics.append(result)
                    continue
            except Exception as exc:
                result.update({"status": "skipped", "reason": f"Broker symbol visibility check failed: {exc}"})
                diagnostics.append(result)
                continue

            signal = detect_fvg(symbol, timeframe)
            result["fvg_detected"] = bool(signal)
            setup_score = score_composite_setup(symbol, signal, timeframe)
            result["setup_score"] = round(float(setup_score.get("score", 0.0) or 0.0), 4)
            early_entry_allowed = os.getenv("FEATURE_EARLY_ENTRY", "false").lower() in ["true", "1", "yes"]
            wait_for_retest = os.getenv("WAIT_FOR_RETEST", "true").lower() in ["true", "1", "yes"]
            if not wait_for_retest and early_entry_allowed and not signal and setup_score.get("score", 0) >= 0.50:
                signal = build_early_entry_signal(symbol, setup_score, timeframe)
                result["early_entry_detected"] = bool(signal)

            if signal:
                result["status"] = "signal"
                result["reason"] = (
                    "FVG detected"
                    if result["fvg_detected"]
                    else f"Early-entry setup detected; score={result['setup_score']:.2f}"
                )
                signal["order_block"] = detect_order_block(symbol, timeframe)
                signal["structure_break"] = detect_market_structure(symbol, timeframe)
                signal["market_structure_shift"] = setup_score.get("market_structure_shift")
                signal["liquidity_sweep"] = setup_score.get("liquidity_sweep")
                signal["trend_strength"] = detect_trend_strength(symbol, timeframe)
                signal["liquidity_zone"] = detect_liquidity_zone(symbol, timeframe)
                signal["divergence"] = detect_divergence(symbol, timeframe)
                signal["higher_timeframe_bias"] = setup_score.get("higher_timeframe_bias")
                signal["session_bias"] = setup_score.get("session_bias")
                signal["displacement"] = setup_score.get("displacement")
                signal["premium_discount"] = setup_score.get("premium_discount")
                signal["false_move"] = setup_score.get("false_move")
                signal["news_move"] = setup_score.get("news_move")
                signal["market_quality"] = setup_score.get("market_quality")
                signal["market_regime"] = setup_score.get("market_regime")
                signal["spread_safety"] = setup_score.get("spread")
                signal["setup_score"] = setup_score
                if os.getenv("ICT_ENABLED", "false").lower() in ["true", "1", "yes"]:
                    ict_df = _fetch_rates(symbol, timeframe, 80)
                    signal["ict"] = analyze_ict(
                        ict_df,
                        symbol,
                        signal.get("action"),
                        setup_score.get("higher_timeframe_bias"),
                        entry_price=signal.get("entry"),
                    )
                    audit_detectors(ict_df, symbol, signal.get("action"), timeframe)
                signal["confluence_score"] = _estimate_confluence(signal)
                signal["conviction"] = max(float(signal.get("confluence_score", 0.0)), float(setup_score.get("score", 0.0)))
                signal["trade_horizon"] = classify_trade_horizon({"symbol": symbol, **signal})
                fingerprint = _signal_fingerprint(
                    symbol,
                    signal.get("action"),
                    signal.get("type") or setup_score.get("archetype") or signal.get("nature"),
                    timeframe,
                    signal.get("candle_timestamp"),
                )
                result["signal_fingerprint"] = fingerprint
                signal["signal_fingerprint"] = fingerprint
                if fingerprint in _EMITTED_SIGNAL_FINGERPRINTS:
                    result["status"] = "duplicate"
                    result["reason"] = "Duplicate signal ignored for same symbol/direction/setup/timeframe/candle"
                    diagnostics.append(result)
                    continue
                _EMITTED_SIGNAL_FINGERPRINTS.add(fingerprint)
                signals.append({**signal, "symbol": symbol, "configured_symbol": configured_symbol})
            else:
                result["status"] = "no_signal"
                result["reason"] = f"No FVG detected; setup score={result['setup_score']:.2f}; early-entry disabled"
            diagnostics.append(result)
        if return_diagnostics:
            return signals, diagnostics
        return signals
    finally:
        _ACTIVE_BROKER = previous_broker


def calculate_scalp_potential(signal):
    """Score scalp potential for a given FVG signal."""
    try:
        entry = float(signal.get("entry", 0))
        sl = float(signal.get("sl", 0))
        tp = float(signal.get("tp", 0))
        symbol = signal.get("symbol")
        if entry == 0 or sl == 0 or tp == 0:
            return {"score": 0.0, "label": "Unknown", "risk_pips": 0, "reward_pips": 0}

        # Pip size estimate by symbol (JPY pairs have 0.01, else 0.0001)
        pip_size = 0.0001
        if symbol and symbol.endswith("JPY"):
            pip_size = 0.01

        risk_pips = abs(entry - sl) / pip_size
        reward_pips = abs(tp - entry) / pip_size
        r_ratio = reward_pips / risk_pips if risk_pips > 0 else 0

        # Scalp candidates = tight risk, minimal swing, good R:R
        if risk_pips <= 20 and r_ratio >= 1.0 and reward_pips <= 80:
            score = min(1.0, (20 - risk_pips) / 20 * 0.6 + min(1.0, r_ratio / 3) * 0.4)
            label = "Scalp Potential" if score >= 0.6 else "Scalp Candidate"
        elif risk_pips <= 35 and r_ratio >= 1.2:
            score = 0.45 + min(1.0, r_ratio / 4) * 0.4
            label = "Momentum Setup"
        else:
            score = min(1.0, min(reward_pips, 200) / 200)
            label = "Trend Opportunity"

        return {
            "score": round(score, 3),
            "label": label,
            "risk_pips": round(risk_pips, 2),
            "reward_pips": round(reward_pips, 2),
            "r_ratio": round(r_ratio, 2),
        }

    except Exception as e:
        logger.error(f"Scalp potential calc fail for {signal.get('symbol')}: {e}")
        return {"score": 0.0, "label": "Error", "risk_pips": 0, "reward_pips": 0}


def classify_trade_horizon(signal):
    """Classify whether a setup is better managed as scalp, intraday, or swing."""
    try:
        entry = float(signal.get("entry", 0))
        sl = float(signal.get("sl", 0))
        tp = float(signal.get("tp", 0))
        symbol = str(signal.get("symbol") or "").upper()
        setup = signal.get("setup_score") or {}
        spread = signal.get("spread_safety") or setup.get("spread") or {}
        session = signal.get("session_bias") or setup.get("session_bias") or {}
        htf = signal.get("higher_timeframe_bias") or setup.get("higher_timeframe_bias") or {}
        archetype = str(setup.get("archetype") or "")

        if not entry or not sl or not tp:
            return {"type": "INTRADAY", "confidence": 0.0, "hold_time": "unknown", "reason": "missing levels"}

        info = _symbol_info(symbol)
        pip_size = _pip_size_for_symbol(symbol, info)
        risk_pips = abs(entry - sl) / pip_size if pip_size else 0
        reward_pips = abs(tp - entry) / pip_size if pip_size else 0
        r_ratio = reward_pips / risk_pips if risk_pips else 0
        spread_safe = spread.get("safe") is not False
        session_score = float(session.get("score", 0.0) or 0.0)
        htf_aligned = htf.get("direction") in ["Bullish", "Bearish"]

        if "XAU" in symbol or "GOLD" in symbol:
            scalp_risk_limit = 90
            scalp_reward_limit = 220
            swing_reward_floor = 450
        elif symbol.endswith("JPY"):
            scalp_risk_limit = 8
            scalp_reward_limit = 25
            swing_reward_floor = 45
        else:
            scalp_risk_limit = 8
            scalp_reward_limit = 25
            swing_reward_floor = 45

        reasons = []
        if spread_safe:
            reasons.append("spread safe")
        if archetype:
            reasons.append(archetype)

        if risk_pips <= scalp_risk_limit and reward_pips <= scalp_reward_limit and r_ratio >= 1.0 and spread_safe:
            confidence = min(1.0, 0.55 + min(0.25, r_ratio / 10) + (0.10 if session_score >= 0.65 else 0))
            return {
                "type": "SCALP",
                "confidence": round(confidence, 2),
                "hold_time": "5-30 min",
                "reason": ", ".join(reasons + [f"tight risk {risk_pips:.1f}p", f"{r_ratio:.2f}R"]),
                "risk_pips": round(risk_pips, 2),
                "reward_pips": round(reward_pips, 2),
                "r_ratio": round(r_ratio, 2),
            }

        if htf_aligned and reward_pips >= swing_reward_floor and r_ratio >= 1.5:
            confidence = min(1.0, 0.55 + min(0.25, r_ratio / 8) + (0.15 if htf_aligned else 0))
            return {
                "type": "SWING",
                "confidence": round(confidence, 2),
                "hold_time": "4h-2d",
                "reason": ", ".join(reasons + ["HTF aligned", f"wide target {reward_pips:.1f}p", f"{r_ratio:.2f}R"]),
                "risk_pips": round(risk_pips, 2),
                "reward_pips": round(reward_pips, 2),
                "r_ratio": round(r_ratio, 2),
            }

        confidence = min(1.0, 0.45 + min(0.25, r_ratio / 8) + (0.10 if session_score >= 0.65 else 0) + (0.10 if htf_aligned else 0))
        return {
            "type": "INTRADAY",
            "confidence": round(confidence, 2),
            "hold_time": "30 min-4h",
            "reason": ", ".join(reasons + [f"session score {session_score:.2f}", f"{r_ratio:.2f}R"]),
            "risk_pips": round(risk_pips, 2),
            "reward_pips": round(reward_pips, 2),
            "r_ratio": round(r_ratio, 2),
        }
    except Exception as e:
        logger.error(f"Trade horizon classification failed for {signal.get('symbol')}: {e}")
        return {"type": "INTRADAY", "confidence": 0.0, "hold_time": "unknown", "reason": str(e)}
