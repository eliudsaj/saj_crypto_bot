"""
Conditional Watchlist Manager - "Smart Watchlist" Feature
Multi-phase conditional execution with market structure analysis.

Phases:
1. Asian High Sweep - Detect price sweeping the Asian session high (00:00-08:00 UTC)
2. Change of Character (mBOS) - Detect market break of structure
3. Extreme FVG Return - Execute only when price returns to extreme FVG zone
"""

import logging
from datetime import datetime, timedelta, timezone
import pandas as pd
import MetaTrader5 as mt5

logger = logging.getLogger(__name__)


class ConditionalWatchlistManager:
    """Multi-phase conditional execution for high-probability trades."""
    
    def __init__(self, mt5_interface):
        """
        Args:
            mt5_interface: MT5Interface instance for price data and orders
        """
        self.mt5 = mt5_interface
        self.watchlist = {}  # Track watchlist symbols and their phases
        
    def initialize_watchlist(self, symbols: list) -> dict:
        """
        Initialize watchlist symbols to Phase 1 (Asian High detection).
        
        Args:
            symbols: List of symbols to monitor
            
        Returns:
            Dictionary of initialized watchlist entries
        """
        initialized = {}
        
        for symbol in symbols:
            if symbol not in self.watchlist:
                self.watchlist[symbol] = {
                    "phase": 1,  # Start at Phase 1
                    "asian_high": None,
                    "asian_low": None,
                    "phase1_started": datetime.now().isoformat(),
                    "sweep_detected": False,
                    "mBOS_level": None,
                    "mBOS_detected": False,
                    "phase2_started": None,
                    "extreme_fvg": None,
                    "phase3_started": None,
                    "ready_for_execution": False,
                }
                initialized[symbol] = self.watchlist[symbol]
        
        return initialized
    
    def phase1_detect_asian_sweep(self, symbol: str) -> bool:
        """
        Phase 1: Detect if price has swept the Asian session high.
        Asian session: 00:00 - 08:00 UTC
        
        Returns:
            True if sweep detected and Phase 1 complete
        """
        try:
            if symbol not in self.watchlist:
                logger.warning(f"Symbol {symbol} not in watchlist")
                return False
            
            watch_entry = self.watchlist[symbol]
            if watch_entry["phase"] != 1:
                return False  # Already past Phase 1
            
            # Fetch last 12 hours of M30 data (24 bars = 12 hours)
            rates = self.mt5.copy_rates_from_pos(symbol, self.mt5.TIMEFRAME_M30, 0, 24)
            if rates is None or len(rates) < 10:
                logger.warning(f"Insufficient M30 data for {symbol}")
                return False
            
            df = pd.DataFrame(rates)
            now_utc = datetime.now(timezone.utc)
            
            # Identify Asian session bars (00:00-08:00 UTC)
            asian_bars = []
            for idx, row in df.iterrows():
                bar_time = datetime.fromtimestamp(int(row["time"]), tz=timezone.utc)
                if bar_time.hour < 8:
                    asian_bars.append(row)
            
            if not asian_bars:
                logger.debug(f"No Asian session bars found for {symbol}")
                return False
            
            asian_df = pd.DataFrame(asian_bars)
            asian_high = float(asian_df["high"].max())
            asian_low = float(asian_df["low"].min())
            
            # Store for later phases
            watch_entry["asian_high"] = asian_high
            watch_entry["asian_low"] = asian_low
            
            # Check if price has swept above Asian high
            current_price = float(df.iloc[-1]["close"])
            sweep_threshold = asian_high
            
            if current_price > sweep_threshold:
                watch_entry["sweep_detected"] = True
                logger.info(f"PHASE 1 COMPLETE for {symbol}: Asian High sweep detected at {current_price:.5f}")
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"Error in Phase 1 detection for {symbol}: {e}")
            return False
    
    def phase2_detect_mbos(self, symbol: str) -> bool:
        """
        Phase 2: Detect Change of Character (Market Break of Structure).
        mBOS occurs when price retraces and breaks the pullback structure.
        
        Returns:
            True if mBOS detected and Phase 2 complete
        """
        try:
            if symbol not in self.watchlist:
                return False
            
            watch_entry = self.watchlist[symbol]
            if watch_entry["phase"] != 2:
                return False  # Not in Phase 2
            
            # Fetch last 20 M30 bars for mBOS detection
            rates = self.mt5.copy_rates_from_pos(symbol, self.mt5.TIMEFRAME_M30, 0, 20)
            if rates is None or len(rates) < 5:
                return False
            
            df = pd.DataFrame(rates)
            df["close"] = df["close"].astype(float)
            df["high"] = df["high"].astype(float)
            df["low"] = df["low"].astype(float)
            
            current_price = float(df.iloc[-1]["close"])
            current_high = float(df.iloc[-1]["high"])
            current_low = float(df.iloc[-1]["low"])
            
            # Look for a pullback candle followed by a breakout
            # mBOS: Lower High or Higher Low as structure break
            significant_change = False
            
            if len(df) >= 5:
                # Check for lower high (bearish mBOS)
                prev_high = float(df.iloc[-3]["high"])
                if current_low < prev_high:
                    significant_change = True
                    logger.info(f"Bearish mBOS structure break detected for {symbol}")
                
                # Check for higher low (bullish mBOS)
                prev_low = float(df.iloc[-3]["low"])
                if current_high > prev_low:
                    significant_change = True
                    logger.info(f"Bullish mBOS structure break detected for {symbol}")
            
            if significant_change:
                watch_entry["mBOS_detected"] = True
                watch_entry["mBOS_level"] = current_price
                watch_entry["phase2_started"] = datetime.now().isoformat()
                logger.info(f"PHASE 2 COMPLETE for {symbol}: mBOS detected at {current_price:.5f}")
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"Error in Phase 2 detection for {symbol}: {e}")
            return False
    
    def phase3_detect_extreme_fvg_return(self, symbol: str) -> bool:
        """
        Phase 3: Detect price return to Extreme FVG zone.
        Execute trade only when price returns to the specific FVG level.
        
        Returns:
            True if price at extreme FVG and ready for execution
        """
        try:
            if symbol not in self.watchlist:
                return False
            
            watch_entry = self.watchlist[symbol]
            if watch_entry["phase"] != 3:
                return False  # Not in Phase 3
            
            # Fetch current price and recent M30 data
            rates = self.mt5.copy_rates_from_pos(symbol, self.mt5.TIMEFRAME_M30, 0, 15)
            if rates is None or len(rates) < 5:
                return False
            
            df = pd.DataFrame(rates)
            current_price = float(df.iloc[-1]["close"])
            
            # Get the Extreme FVG level (detect fresh FVG after mBOS)
            extreme_fvg = self._detect_extreme_fvg(symbol, df)
            if not extreme_fvg:
                return False
            
            watch_entry["extreme_fvg"] = extreme_fvg
            
            # Check if price has returned to the extreme FVG zone
            fvg_low = extreme_fvg["entry"] - (extreme_fvg["gap_size"] * 0.5)
            fvg_high = extreme_fvg["entry"] + (extreme_fvg["gap_size"] * 0.5)
            
            if fvg_low <= current_price <= fvg_high:
                watch_entry["ready_for_execution"] = True
                watch_entry["phase3_started"] = datetime.now().isoformat()
                logger.info(f"PHASE 3 COMPLETE for {symbol}: Price at Extreme FVG {current_price:.5f}")
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"Error in Phase 3 detection for {symbol}: {e}")
            return False
    
    def process_watchlist(self) -> dict:
        """
        Process all symbols in watchlist through their phases.
        Automatically advance phases based on conditions.
        
        Returns:
            Dictionary of phase updates
        """
        updates = {}
        
        for symbol in list(self.watchlist.keys()):
            try:
                watch_entry = self.watchlist[symbol]
                current_phase = watch_entry["phase"]
                
                # Phase 1: Detect Asian High sweep
                if current_phase == 1:
                    if self.phase1_detect_asian_sweep(symbol):
                        watch_entry["phase"] = 2
                        updates[symbol] = {"phase": 2, "event": "Advanced to Phase 2"}
                
                # Phase 2: Detect mBOS
                elif current_phase == 2:
                    if self.phase2_detect_mbos(symbol):
                        watch_entry["phase"] = 3
                        updates[symbol] = {"phase": 3, "event": "Advanced to Phase 3"}
                
                # Phase 3: Detect return to Extreme FVG
                elif current_phase == 3:
                    if self.phase3_detect_extreme_fvg_return(symbol):
                        updates[symbol] = {"phase": 3, "event": "Ready for execution"}
            
            except Exception as e:
                logger.error(f"Error processing watchlist for {symbol}: {e}")
        
        return updates
    
    def get_ready_for_execution(self) -> list:
        """Return list of symbols ready for execution (Phase 3 complete)."""
        ready = []
        for symbol, watch_entry in self.watchlist.items():
            if watch_entry["ready_for_execution"]:
                ready.append({
                    "symbol": symbol,
                    "extreme_fvg": watch_entry["extreme_fvg"],
                    "mBOS_level": watch_entry["mBOS_level"],
                    "asian_high": watch_entry["asian_high"],
                })
        return ready
    
    def place_conditional_order(self, symbol: str, volume: float) -> dict:
        """
        Place a limit order for a symbol ready for execution.
        Uses the extreme FVG zone as entry point.
        
        Returns:
            Dictionary with order details
        """
        try:
            if symbol not in self.watchlist:
                logger.error(f"Symbol {symbol} not in watchlist")
                return {}
            
            watch_entry = self.watchlist[symbol]
            if not watch_entry["ready_for_execution"]:
                logger.warning(f"Symbol {symbol} not ready for execution")
                return {}
            
            extreme_fvg = watch_entry["extreme_fvg"]
            action = extreme_fvg["action"]
            entry = extreme_fvg["entry"]
            sl = extreme_fvg["sl"]
            tp = extreme_fvg["tp"]
            
            # Place limit order
            if action == "BUY":
                ticket = self.mt5.place_buy_limit_order(symbol, volume, entry, sl, tp)
            else:
                ticket = self.mt5.place_sell_limit_order(symbol, volume, entry, sl, tp)
            
            if ticket:
                logger.info(
                    f"Conditional order placed for {symbol}: "
                    f"{action} Limit at {entry:.5f}, SL={sl:.5f}, TP={tp:.5f}"
                )
                return {
                    "symbol": symbol,
                    "ticket": ticket,
                    "action": action,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "volume": volume,
                    "placed_at": datetime.now().isoformat(),
                }
            
            return {}
        
        except Exception as e:
            logger.error(f"Error placing conditional order for {symbol}: {e}")
            return {}
    
    def reset_symbol(self, symbol: str) -> bool:
        """Reset a symbol back to Phase 1 after trade completion."""
        try:
            if symbol in self.watchlist:
                self.watchlist[symbol] = {
                    "phase": 1,
                    "asian_high": None,
                    "asian_low": None,
                    "phase1_started": datetime.now().isoformat(),
                    "sweep_detected": False,
                    "mBOS_level": None,
                    "mBOS_detected": False,
                    "phase2_started": None,
                    "extreme_fvg": None,
                    "phase3_started": None,
                    "ready_for_execution": False,
                }
                logger.info(f"Reset {symbol} to Phase 1")
                return True
            return False
        
        except Exception as e:
            logger.error(f"Error resetting symbol {symbol}: {e}")
            return False
    
    def get_watchlist_summary(self) -> list:
        """Return summary of all watchlist entries and their current phases."""
        return [
            {
                "symbol": symbol,
                "phase": entry["phase"],
                "sweep_detected": entry["sweep_detected"],
                "mBOS_detected": entry["mBOS_detected"],
                "ready_for_execution": entry["ready_for_execution"],
                "phase1_started": entry["phase1_started"],
            }
            for symbol, entry in self.watchlist.items()
        ]
    
    def _detect_extreme_fvg(self, symbol: str, df: pd.DataFrame) -> dict:
        """
        Detect the extreme/freshest FVG after mBOS.
        This is the most recent powerful gap that hasn't been filled.
        """
        try:
            fvgs = []
            
            for i in range(len(df) - 3):
                candle_2 = df.iloc[i]
                candle_1 = df.iloc[i + 1]
                current = df.iloc[i + 2]
                
                low_2 = float(candle_2["low"])
                high_2 = float(candle_2["high"])
                low_1 = float(candle_1["low"])
                high_1 = float(candle_1["high"])
                close_current = float(current["close"])
                
                # Bullish FVG
                if low_2 > high_1:
                    gap_size = low_2 - high_1
                    if gap_size > 0:
                        fvgs.append({
                            "type": "BULLISH",
                            "action": "BUY",
                            "entry": high_1,
                            "sl": low_1,
                            "tp": low_2 + (gap_size * 3),
                            "gap_size": gap_size,
                            "recency": i,  # Lower index = more recent
                        })
                
                # Bearish FVG
                if high_2 < low_1:
                    gap_size = low_1 - high_2
                    if gap_size > 0:
                        fvgs.append({
                            "type": "BEARISH",
                            "action": "SELL",
                            "entry": low_1,
                            "sl": high_1,
                            "tp": high_2 - (gap_size * 3),
                            "gap_size": gap_size,
                            "recency": i,
                        })
            
            # Return most recent (freshest) FVG
            if fvgs:
                fvgs.sort(key=lambda x: x["recency"])
                return fvgs[0]
            
            return None
        
        except Exception as e:
            logger.error(f"Error detecting extreme FVG for {symbol}: {e}")
            return None
