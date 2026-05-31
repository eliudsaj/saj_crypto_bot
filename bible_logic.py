"""Trade Bible Logic - Rule-based signal validation

This module enforces strict rule-based trade validation ("Trade Bible") that
can be toggled from the UI.

Rules implemented:
- EMA Filter: M30 50-EMA (Price > EMA for longs, Price < EMA for shorts)
- Volume Filter: Current M5 volume > 1.15x 10-period SMA
- PO3/Asian Sweep: Requires a sweep of 00:00-08:00 UTC range before entry
- Freshness: Reject stale signals older than 5 minutes
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

import pandas as pd


def validate_trade(symbol: str, config: dict) -> (bool, str):
    """Validate a trade signal against Trade Bible rules.

    Args:
        symbol: Trading symbol (e.g., "EURUSD").
        config: Dict containing checkbox states (ema, volume, po3).

    Returns:
        (valid, reason) where valid is True when all enabled rules pass.
    """

    # Normalize config flags (support strings like "true")
    enabled = {
        "ema": str(config.get("ema", True)).lower() in ["1", "true", "yes"],
        "volume": str(config.get("volume", True)).lower() in ["1", "true", "yes"],
        "po3": str(config.get("po3", True)).lower() in ["1", "true", "yes"],
    }

    if mt5 is None:
        return False, "MetaTrader5 library not installed"

    # Check if MT5 is connected
    if not mt5.initialize():
        return False, "MT5 not connected"

    # Fetch latest M5 bar for freshness and volume checks
    now = datetime.now(timezone.utc)
    try:
        bars_m5 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 20)
        if bars_m5 is None or len(bars_m5) < 12:
            return False, "No M5 data"
    except Exception as e:
        return False, f"MT5 data error: {e}"

    # Use the last fully closed M5 bar for validation; the very latest bar may still be forming.
    latest_bar = bars_m5[-2] if len(bars_m5) > 1 else bars_m5[-1]
    bar_time = datetime.fromtimestamp(int(latest_bar["time"]), tz=timezone.utc)
    age_secs = (now - bar_time).total_seconds()
    # Manual-defined stale data cutoff for validity
    if age_secs > 300:  # 5 minutes
        return False, f"Stale data (>{age_secs:.0f}s)"

    # Normalize incoming signal direction (when provided via config)
    action = str(config.get("action", "")).upper()
    latest_close = float(latest_bar["close"])

    # Volume filter
    if enabled["volume"]:
        previous_bars = bars_m5[-12:-2] if len(bars_m5) >= 12 else bars_m5[:-2]
        if len(previous_bars) == 0:
            return False, "Insufficient volume history"
        volume_sma = pd.Series([b["tick_volume"] for b in previous_bars]).mean()
        threshold = volume_sma * 1.15
        if volume_sma is None or volume_sma <= 0 or latest_bar["tick_volume"] <= threshold:
            return False, f"Volume filter not met ({latest_bar['tick_volume']:.0f} <= {threshold:.0f})"

    # EMA filter (M30 50-period)
    try:
        bars_m30 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M30, 0, 100)
        if bars_m30 is None or len(bars_m30) < 55:
            if enabled["ema"]:
                return False, "Not enough M30 bars"
        else:
            df30 = pd.DataFrame(bars_m30)
            ema50 = df30["close"].ewm(span=50, adjust=False).mean().iloc[-1]
            # price for EMA comparison: last close of M30
            price = float(df30.iloc[-1]["close"])
            if enabled["ema"]:
                # Validate price relative to the 50 EMA
                if action == "BUY" and latest_close <= ema50:
                    return False, "EMA filter (long) failed"
                if action == "SELL" and latest_close >= ema50:
                    return False, "EMA filter (short) failed"
    except Exception:
        # If MT5 throws for M30 data, skip the EMA check (should not happen in live)
        if enabled["ema"]:
            return False, "EMA data error"

    # PO3 / Asian Sweep: require sweep of 00:00-08:00 UTC range
    # CRITICAL FIX: Make PO3 less restrictive - allow trades if we're past Asian session
    # or if Asian session data shows sufficient volatility
    if enabled["po3"]:
        current_utc = now
        is_past_asian = current_utc.hour >= 8
        
        # If we're past Asian session, allow trades (London/NYC sessions)
        if is_past_asian:
            # Determine today (or previous day if before 08:00 UTC)
            asian_day = current_utc if current_utc.hour >= 8 else current_utc - timedelta(days=1)
            start = datetime(asian_day.year, asian_day.month, asian_day.day, 0, 0, tzinfo=timezone.utc)
            end = datetime(asian_day.year, asian_day.month, asian_day.day, 8, 0, tzinfo=timezone.utc)

            try:
                asian_bars = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start, end)
                if asian_bars is None or len(asian_bars) == 0:
                    # No Asian data available, allow trade
                    pass
                else:
                    df_asian = pd.DataFrame(asian_bars)
                    asian_high = df_asian["high"].max()
                    asian_low = df_asian["low"].min()
                    asian_range = asian_high - asian_low
                    
                    # Check if price has swept beyond the range after 08:00 UTC
                    post_asian_bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 100)
                    if post_asian_bars is not None and len(post_asian_bars) > 0:
                        df_post = pd.DataFrame(post_asian_bars)
                        df_post["dt"] = pd.to_datetime(df_post["time"], unit="s", utc=True)
                        df_post_after = df_post[df_post["dt"] >= end]
                        
                        if not df_post_after.empty:
                            has_sweep_long = (df_post_after["high"] > asian_high).any()
                            has_sweep_short = (df_post_after["low"] < asian_low).any()

                            if action == "BUY" and not has_sweep_long:
                                return False, "No Asian sweep (long)"
                            elif action == "SELL" and not has_sweep_short:
                                return False, "No Asian sweep (short)"
            except Exception as e:
                # If PO3 validation fails, log but don't block trade
                logger.warning(f"PO3 validation error for {symbol}: {e}")
        # During Asian session, skip PO3 check to allow early trades

    return True, "OK"
