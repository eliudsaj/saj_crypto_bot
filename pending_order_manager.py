"""
Pending Order Manager - "Set and Forget" Feature
Places Limit Orders on MT5 based on calculated Trade Bible zones.
Orders persist on the broker even when the bot is offline.
"""

import logging
from datetime import datetime, timedelta
import pandas as pd
import MetaTrader5 as mt5

logger = logging.getLogger(__name__)


class PendingOrderManager:
    """Manages pending limit orders for high-probability FVG zones."""
    
    def __init__(self, mt5_interface):
        """
        Args:
            mt5_interface: MT5Interface instance for order management
        """
        self.mt5 = mt5_interface
        self.pending_orders = {}  # Track pending orders by symbol
        
    def identify_high_probability_zones(self, symbol: str, timeframe=None, rr_ratio: float = 2.0):
        """
        Calculate high-probability zones based on M30 FVGs.
        Returns list of potential zones with entry, SL, and TP.
        """
        try:
            timeframe = timeframe or self.mt5.TIMEFRAME_M30
            # Fetch M30 data - last 10 bars
            rates = self.mt5.copy_rates_from_pos(symbol, timeframe, 0, 10)
            if rates is None or len(rates) < 5:
                logger.warning(f"Insufficient M30 data for {symbol}")
                return []
            
            df = pd.DataFrame(rates)
            zones = []
            
            # Scan for FVGs in the last 5 bars
            for i in range(len(df) - 3):
                candle_2_bars_ago = df.iloc[i]
                candle_1_bar_ago = df.iloc[i + 1]
                current_candle = df.iloc[i + 2]
                
                low_2 = float(candle_2_bars_ago["low"])
                high_2 = float(candle_2_bars_ago["high"])
                low_1 = float(candle_1_bar_ago["low"])
                high_1 = float(candle_1_bar_ago["high"])
                low_current = float(current_candle["low"])
                high_current = float(current_candle["high"])
                close_current = float(current_candle["close"])
                
                pip_size = self._get_pip_size(symbol)
                
                # BULLISH FVG: Low of 2 bars ago > High of 1 bar ago
                if low_2 > high_1:
                    gap_size = low_2 - high_1
                    entry = high_1  # Entry at top of the FVG
                    # Add spread buffer for BUY_LIMIT (triggered by ask)
                    spread_buffer = pip_size * 0.2  # 0.2 pip buffer
                    entry += spread_buffer
                    sl = low_1  # SL below the sweep candle
                    risk_distance = abs(entry - sl)
                    tp = entry + (risk_distance * rr_ratio)
                    
                    # Only consider if price hasn't already swept the zone
                    if close_current >= entry:
                        zones.append({
                            "type": "BULLISH_FVG",
                            "action": "BUY",
                            "entry": entry,
                            "sl": sl,
                            "tp": tp,
                            "gap_size": gap_size,
                            "probability": self._calculate_probability_score(
                                symbol, "BUY", entry, sl, tp
                            ),
                            "bar_time": datetime.fromtimestamp(int(current_candle["time"])),
                        })
                
                # BEARISH FVG: High of 2 bars ago < Low of 1 bar ago
                if high_2 < low_1:
                    gap_size = low_1 - high_2
                    entry = low_1  # Entry at bottom of the FVG
                    # Add spread buffer for SELL_LIMIT (triggered by bid)
                    spread_buffer = pip_size * 0.2  # 0.2 pip buffer
                    entry -= spread_buffer
                    sl = high_1  # SL above the sweep candle
                    risk_distance = abs(sl - entry)
                    tp = entry - (risk_distance * rr_ratio)
                    
                    # Only consider if price hasn't already swept the zone
                    if close_current <= entry:
                        zones.append({
                            "type": "BEARISH_FVG",
                            "action": "SELL",
                            "entry": entry,
                            "sl": sl,
                            "tp": tp,
                            "gap_size": gap_size,
                            "probability": self._calculate_probability_score(
                                symbol, "SELL", entry, sl, tp
                            ),
                            "bar_time": datetime.fromtimestamp(int(current_candle["time"])),
                        })
            
            # Sort by probability (highest first)
            zones.sort(key=lambda x: x["probability"], reverse=True)
            return zones
        
        except Exception as e:
            logger.error(f"Error identifying high-probability zones for {symbol}: {e}")
            return []
    
    def count_pending_orders(self, symbol: str) -> int:
        """Return how many pending orders currently exist for a symbol."""
        try:
            orders = self.mt5.orders_get(symbol=symbol)
            if orders is None:
                return 0
            return len(orders)
        except Exception:
            # Fallback to internal tracking if MT5 call fails
            if symbol in self.pending_orders:
                return 1
            return 0

    def place_pending_order(self, symbol: str, zone: dict, volume: float) -> bool:
        """
        Place a pending limit order on MT5.
        
        Args:
            symbol: Trading symbol
            zone: Zone dict with entry, sl, tp, action
            volume: Order volume
            
        Returns:
            True if order placed successfully
        """
        try:
            existing_count = self.count_pending_orders(symbol)
            if existing_count >= 3:
                logger.warning(f"Skipping new pending order for {symbol}: already {existing_count} pending orders")
                return False

            action = zone["action"]
            entry = zone["entry"]
            sl = zone["sl"]
            tp = zone["tp"]
            
            if action == "BUY":
                ticket = self.mt5.place_buy_limit_order(symbol, volume, entry, sl, tp)
            else:
                ticket = self.mt5.place_sell_limit_order(symbol, volume, entry, sl, tp)
            
            if ticket:
                self.pending_orders[symbol] = {
                    "ticket": ticket,
                    "action": action,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "volume": volume,
                    "zone_type": zone.get("type"),
                    "placed_at": datetime.now().isoformat(),
                }
                logger.info(
                    f"Pending {action} order placed for {symbol}: "
                    f"Entry={entry:.5f}, SL={sl:.5f}, TP={tp:.5f}"
                )
                return True
            else:
                logger.error(f"Failed to place pending order for {symbol}")
                return False
        
        except Exception as e:
            logger.error(f"Error placing pending order for {symbol}: {e}")
            return False
    
    def scan_and_place_pending_orders(self, symbols: list, volume_func, rr_ratio: float = 2.0) -> dict:
        """
        Scan symbols for high-probability zones and place pending orders.
        
        Args:
            symbols: List of symbols to scan
            volume_func: Function to calculate volume given symbol and SL/Entry
            
        Returns:
            Dictionary of placed orders per symbol
        """
        placed_orders = {}
        
        for symbol in symbols:
            try:
                # Skip if there are already too many pending orders for this symbol
                existing_count = self.count_pending_orders(symbol)
                if existing_count >= 3:
                    logger.info(f"Skipping pending order placement for {symbol}: {existing_count} pending orders already present")
                    continue

                # Skip if order already pending for this symbol in manager tracking
                if symbol in self.pending_orders:
                    logger.debug(f"Pending order already exists for {symbol}")
                    continue
                
                # Identify high-probability zones
                zones = self.identify_high_probability_zones(symbol, rr_ratio=rr_ratio)
                if not zones:
                    continue
                
                # Place order on highest probability zone
                best_zone = zones[0]
                
                # Calculate volume
                volume = volume_func(symbol, best_zone["sl"], best_zone["entry"])
                if volume <= 0:
                    logger.warning(f"Skipping pending order for {symbol}: invalid lot size {volume}")
                    continue
                
                if self.place_pending_order(symbol, best_zone, volume):
                    placed_orders[symbol] = best_zone
            
            except Exception as e:
                logger.error(f"Error processing pending order for {symbol}: {e}")
        
        return placed_orders
    
    def monitor_pending_orders(self) -> dict:
        """
        Monitor all pending orders and update their status.
        Also cancels orders that are too old or price has moved away.
        
        Returns:
            Dictionary with order status updates
        """
        try:
            orders = self.mt5.orders_get()
            if not orders:
                logger.debug("No pending orders found on MT5")
                return {}
            
            updates = {}
            order_tickets = {o.ticket for o in orders}
            
            # Track which orders are still active
            for symbol, order_info in list(self.pending_orders.items()):
                ticket = order_info["ticket"]
                
                if ticket in order_tickets:
                    # Order still pending - check if it should be cancelled
                    for order in orders:
                        if order.ticket == ticket:
                            # Check if order is too old (24 hours)
                            placed_time = datetime.fromisoformat(order_info["placed_at"])
                            if datetime.now() - placed_time > timedelta(hours=24):
                                logger.info(f"Cancelling old pending order for {symbol} (24h timeout)")
                                self.cancel_pending_order(symbol)
                                updates[symbol] = {
                                    "status": "CANCELLED_TIMEOUT",
                                    "cancelled_at": datetime.now().isoformat(),
                                }
                                continue
                            
                            # Check if price has moved too far away (50 pips for forex)
                            tick = self.mt5.get_symbol_tick(symbol)
                            if tick is None:
                                continue
                            current_price = tick.bid if order.type == self.mt5.ORDER_TYPE_BUY_LIMIT else tick.ask
                            entry_price = order.price_open
                            pip_size = self._get_pip_size(symbol)
                            distance = abs(current_price - entry_price) / pip_size
                            
                            if distance > 50:  # 50 pip threshold
                                logger.info(f"Cancelling pending order for {symbol} - price moved {distance:.1f} pips away")
                                self.cancel_pending_order(symbol)
                                updates[symbol] = {
                                    "status": "CANCELLED_PRICE_MOVE",
                                    "cancelled_at": datetime.now().isoformat(),
                                    "price_distance": distance,
                                }
                                continue
                            
                            updates[symbol] = {
                                "status": "PENDING",
                                "current_price": current_price,
                                "created_time": order.time_setup,
                                "age_hours": (datetime.now() - placed_time).total_seconds() / 3600,
                            }
                else:
                    # Order was filled or cancelled
                    updates[symbol] = {
                        "status": "FILLED_OR_CANCELLED",
                        "removed_at": datetime.now().isoformat(),
                    }
                    del self.pending_orders[symbol]
            
            return updates
        
        except Exception as e:
            logger.error(f"Error monitoring pending orders: {e}")
            return {}
    
    def cancel_pending_order(self, symbol: str) -> bool:
        """Cancel a pending order for a symbol."""
        try:
            if symbol not in self.pending_orders:
                logger.warning(f"No pending order found for {symbol}")
                return False
            
            ticket = self.pending_orders[symbol]["ticket"]
            
            if self.mt5.cancel_order(ticket):
                logger.info(f"Pending order {ticket} cancelled for {symbol}")
                del self.pending_orders[symbol]
                return True
            logger.error(f"Failed to cancel pending order: {ticket}")
            return False
        
        except Exception as e:
            logger.error(f"Error cancelling pending order for {symbol}: {e}")
            return False
    
    def get_pending_orders_summary(self) -> list:
        """Return summary of all pending orders."""
        return [
            {
                "symbol": symbol,
                **order_info
            }
            for symbol, order_info in self.pending_orders.items()
        ]
    
    def _get_pip_size(self, symbol: str):
        """Return pip size for the symbol."""
        try:
            info = self.mt5.get_symbol_info(symbol)
            if not info:
                return 0.0001
            digits = getattr(info, "digits", 5)
            return 0.0001 if digits > 3 else 0.01
        except Exception:
            return 0.0001
    
    def _calculate_probability_score(self, symbol: str, action: str, entry: float, sl: float, tp: float) -> float:
        """
        Calculate a probability score for a trade (0.0 to 1.0).
        Considers: R:R ratio, EMA alignment, recent volatility
        """
        try:
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            
            if risk == 0:
                return 0.0
            
            rr_ratio = reward / risk
            # Ideal R:R is 1:3
            ideal_rr = 3
            rr_score = min(rr_ratio / ideal_rr, 1.0)  # Max score at 1:3
            
            # EMA alignment (check if price is on right side of 50 EMA on M30)
            try:
                bars_m30 = self.mt5.copy_rates_from_pos(symbol, self.mt5.TIMEFRAME_M30, 0, 100)
                if bars_m30 and len(bars_m30) >= 55:
                    df30 = pd.DataFrame(bars_m30)
                    latest_close = float(df30["close"].iloc[-1])
                    ema50 = df30["close"].ewm(span=50, adjust=False).mean().iloc[-1]
                    
                    if action == "BUY" and latest_close > ema50:
                        ema_score = 0.8  # Bullish alignment
                    elif action == "SELL" and latest_close < ema50:
                        ema_score = 0.8  # Bearish alignment
                    else:
                        ema_score = 0.3  # Against EMA
                else:
                    ema_score = 0.5
            except Exception:
                ema_score = 0.5
            
            # Combine scores
            probability = (rr_score * 0.6) + (ema_score * 0.4)
            return round(probability, 2)
        
        except Exception as e:
            logger.error(f"Error calculating probability score: {e}")
            return 0.5
