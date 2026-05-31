"""
Analytic Engine - Market Structure Quality Scoring

This module evaluates current market conditions using Trade Bible rules
and assigns quality scores (0.0 to 1.0) to different aspects of the setup.
"""
import logging
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

import pandas as pd

logger = logging.getLogger(__name__)


class AnalyticEngine:
    """Evaluates market structure quality using traditional trading rules."""

    def __init__(self):
        self.weights = {
            "liquidity": 0.4,    # Asian sweep quality
            "volume": 0.3,       # Volume confirmation
            "fvg": 0.3,          # Fair Value Gap presence
        }

    def evaluate_setup(self, symbol: str, signal: Dict) -> Dict:
        """
        Evaluate the overall setup quality for a trading signal.

        Args:
            symbol: Trading symbol
            signal: Signal dictionary from technical analysis

        Returns:
            Dict with scores and overall quality
        """
        scores = {}

        # Liquidity Check: Asian sweep quality
        scores["liquidity"] = self._score_liquidity(symbol)

        # Volume Profile: Current volume vs average
        scores["volume"] = self._score_volume(symbol)

        # FVG Quality: Gap size and positioning
        scores["fvg"] = self._score_fvg(signal)

        # Calculate weighted overall score
        overall_score = sum(scores[aspect] * weight for aspect, weight in self.weights.items())

        result = {
            "overall_score": overall_score,
            "component_scores": scores,
            "quality_description": self._describe_quality(overall_score),
            "recommendation": "TRADE" if overall_score >= 0.7 else "WAIT"
        }

        logger.info(f"Analytic evaluation for {symbol}: {overall_score:.3f} - {result['quality_description']}")
        return result

    def _score_liquidity(self, symbol: str) -> float:
        """
        Score Asian session liquidity (0.0-1.0).
        1.0 = Perfect sweep of Asian range
        0.0 = No sweep, price hovering
        """
        try:
            if not mt5 or not mt5.initialize():
                return 0.5  # Neutral score if no MT5

            # Get current time and determine Asian session
            now = datetime.now(timezone.utc)
            current_utc = now

            # Determine Asian session (00:00-08:00 UTC)
            if current_utc.hour < 8:
                # Before 08:00, use previous day
                asian_day = current_utc - timedelta(days=1)
            else:
                asian_day = current_utc

            start = datetime(asian_day.year, asian_day.month, asian_day.day, 0, 0, tzinfo=timezone.utc)
            end = datetime(asian_day.year, asian_day.month, asian_day.day, 8, 0, tzinfo=timezone.utc)

            # Get Asian session data
            asian_bars = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start, end)
            if asian_bars is None or len(asian_bars) < 10:
                return 0.3  # Insufficient data

            df_asian = pd.DataFrame(asian_bars)
            asian_high = df_asian["high"].max()
            asian_low = df_asian["low"].min()
            asian_range = asian_high - asian_low

            if asian_range <= 0:
                return 0.0

            # Get post-Asian data (after 08:00)
            post_start = end
            post_end = now
            post_bars = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, post_start, post_end)

            if post_bars is None or len(post_bars) == 0:
                return 0.1  # No post-Asian data yet

            df_post = pd.DataFrame(post_bars)

            # Calculate sweep quality
            high_break = (df_post["high"] > asian_high).any()
            low_break = (df_post["low"] < asian_low).any()

            # Distance from range (how far price moved beyond Asian levels)
            current_price = df_post.iloc[-1]["close"]
            high_distance = max(0, current_price - asian_high) / asian_range
            low_distance = max(0, asian_low - current_price) / asian_range

            sweep_distance = max(high_distance, low_distance)

            # Combine factors
            base_score = 0.2  # Minimum score
            if high_break or low_break:
                base_score = 0.6
            if high_break and low_break:
                base_score = 0.8

            # Add distance bonus (up to 0.2)
            distance_bonus = min(0.2, sweep_distance * 0.5)

            final_score = min(1.0, base_score + distance_bonus)

            logger.debug(f"Liquidity score for {symbol}: {final_score:.3f} (breaks: {high_break}/{low_break}, distance: {sweep_distance:.3f})")
            return final_score

        except Exception as e:
            logger.warning(f"Error calculating liquidity score: {e}")
            return 0.5  # Neutral score on error

    def _score_volume(self, symbol: str) -> float:
        """
        Score volume confirmation (0.0-1.0).
        1.0 = Very high volume (2x+ average)
        0.0 = Very low volume (<0.5x average)
        """
        try:
            if not mt5 or not mt5.initialize():
                return 0.5

            # Get recent M5 bars for volume analysis
            bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 50)
            if bars is None or len(bars) < 25:
                return 0.5

            df = pd.DataFrame(bars)
            volumes = df["tick_volume"]

            # Calculate volume metrics
            current_volume = volumes.iloc[-1]
            avg_volume_20 = volumes.iloc[-21:-1].mean()
            avg_volume_10 = volumes.iloc[-11:-1].mean()

            if avg_volume_20 <= 0 or avg_volume_10 <= 0:
                return 0.5

            # Score based on how much current volume exceeds average
            ratio_20 = current_volume / avg_volume_20
            ratio_10 = current_volume / avg_volume_10

            # Use the higher ratio for scoring
            volume_ratio = max(ratio_20, ratio_10)

            # Convert ratio to score (0.0-1.0)
            if volume_ratio >= 2.0:
                score = 1.0
            elif volume_ratio >= 1.5:
                score = 0.8
            elif volume_ratio >= 1.2:
                score = 0.6
            elif volume_ratio >= 0.8:
                score = 0.4
            elif volume_ratio >= 0.5:
                score = 0.2
            else:
                score = 0.0

            logger.debug(f"Volume score for {symbol}: {score:.3f} (ratio: {volume_ratio:.2f})")
            return score

        except Exception as e:
            logger.warning(f"Error calculating volume score: {e}")
            return 0.5

    def _score_fvg(self, signal: Dict) -> float:
        """
        Score FVG quality (0.0-1.0).
        Based on gap size, positioning, and confluence.
        """
        try:
            if not signal:
                return 0.0

            symbol = signal.get("symbol", "")
            gap_size = signal.get("gap_size", 0)
            nature = signal.get("nature", "")

            # Base score from gap size
            if gap_size <= 0:
                return 0.0

            # Get pip size for the symbol
            pip_size = self._get_pip_size(symbol) or 0.0001
            gap_pips = gap_size / pip_size

            # Score based on gap size in pips
            if gap_pips >= 5:
                size_score = 1.0
            elif gap_pips >= 3:
                size_score = 0.8
            elif gap_pips >= 1.5:
                size_score = 0.6
            elif gap_pips >= 0.8:
                size_score = 0.4
            else:
                size_score = 0.2

            # Bonus for nature (pullback > retest > breakout)
            nature_bonus = 0.0
            if "Pullback" in nature:
                nature_bonus = 0.2
            elif "Retest" in nature:
                nature_bonus = 0.1
            elif "Breakout" in nature:
                nature_bonus = 0.0

            # Trend context bonus
            context_bonus = 0.0
            if "Continuation" in nature:
                context_bonus = 0.1
            elif "Reversal" in nature:
                context_bonus = 0.05

            final_score = min(1.0, size_score + nature_bonus + context_bonus)

            logger.debug(f"FVG score for {symbol}: {final_score:.3f} (size: {gap_pips:.1f}p, nature: {nature})")
            return final_score

        except Exception as e:
            logger.warning(f"Error calculating FVG score: {e}")
            return 0.3

    def _get_pip_size(self, symbol: str) -> float:
        """Get pip size for symbol."""
        try:
            if not mt5 or not mt5.initialize():
                return None
            info = mt5.symbol_info(symbol)
            if info and hasattr(info, 'digits'):
                return 0.0001 if info.digits > 3 else 0.01
        except:
            pass
        return 0.0001  # Default

    def _describe_quality(self, score: float) -> str:
        """Convert score to human-readable description."""
        if score >= 0.9:
            return "Excellent setup - all conditions perfect"
        elif score >= 0.8:
            return "Very good setup - minor weaknesses"
        elif score >= 0.7:
            return "Good setup - acceptable for trade"
        elif score >= 0.6:
            return "Fair setup - monitor closely"
        elif score >= 0.5:
            return "Poor setup - wait for better"
        elif score >= 0.4:
            return "Very poor setup - avoid"
        else:
            return "Terrible setup - no trade"