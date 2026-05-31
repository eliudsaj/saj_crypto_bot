"""MT5 connection and order-management wrapper."""

from __future__ import annotations

import logging
import os
import time

import MetaTrader5 as mt5
from dotenv import load_dotenv

from alerts.manager import alert_manager

load_dotenv()
logger = logging.getLogger(__name__)


class MT5Interface:
    def __init__(self):
        self.is_connected = False
        self.last_order_error = None
        self.account = os.getenv("MT5_ACCOUNT")
        self.password = os.getenv("MT5_PASSWORD")
        self.server = os.getenv("MT5_SERVER")
        self._connection_retry_count = 0

    def _mt5_const(self, name: str, default=None):
        """Read MT5 constants defensively across package versions."""
        return getattr(mt5, name, default)

    def _retcode_done(self, retcode) -> bool:
        return retcode == self._mt5_const("TRADE_RETCODE_DONE", 10009)

    def _retcode_placed_or_done(self, retcode) -> bool:
        return retcode in {
            self._mt5_const("TRADE_RETCODE_DONE", 10009),
            self._mt5_const("TRADE_RETCODE_PLACED", 10008),
        }

    def _get_filling_mode(self, symbol: str):
        try:
            info = mt5.symbol_info(symbol)
            if info is None:
                return self._mt5_const("ORDER_FILLING_IOC", 1)
            return info.filling_mode
        except Exception:
            return self._mt5_const("ORDER_FILLING_IOC", 1)

    def _safe_order_send(self, request: dict, context: str):
        """Call MT5 order_send defensively and keep a readable last error."""
        try:
            result = mt5.order_send(request)
        except Exception as e:
            self.last_order_error = f"{context}: MT5 order_send exception: {e}"
            logger.error(self.last_order_error, exc_info=True)
            return None

        if result is None:
            try:
                last_error = mt5.last_error()
            except Exception:
                last_error = None
            detail = f" last_error={last_error}" if last_error else ""
            self.last_order_error = f"{context}: No response from MT5.{detail}"
            logger.error(self.last_order_error)
        return result

    def _trade_retcode_messages(self):
        retcodes = [
            ("TRADE_RETCODE_NO_MONEY", "Insufficient margin/funds"),
            ("TRADE_RETCODE_INVALID_VOLUME", "Invalid volume for symbol"),
            ("TRADE_RETCODE_MARKET_CLOSED", "Market is closed"),
            ("TRADE_RETCODE_PRICE_CHANGED", "Price changed before execution"),
            ("TRADE_RETCODE_PRICES_CHANGED", "Price changed before execution"),
            ("TRADE_RETCODE_INVALID_EXPIRATION", "Invalid order expiration"),
            ("TRADE_RETCODE_ORDER_CHANGED", "Order was changed"),
            ("TRADE_RETCODE_TOO_MANY_REQUESTS", "Too many requests to MT5"),
            ("TRADE_RETCODE_NO_CHANGES", "No changes to apply"),
            ("TRADE_RETCODE_TRADE_DISABLED", "Trading is disabled"),
        ]
        return {
            value: message
            for name, message in retcodes
            for value in [getattr(mt5, name, None)]
            if value is not None
        }

    def _trade_error_message(self, result):
        error_messages = self._trade_retcode_messages()
        retcode = getattr(result, "retcode", None)
        return error_messages.get(retcode, getattr(result, "comment", None) or f"Unknown error {retcode}")

    def get_symbol_info(self, symbol: str):
        try:
            return mt5.symbol_info(symbol)
        except Exception as e:
            logger.error(f"Error fetching symbol info for {symbol}: {e}")
            return None

    def get_symbol_tick(self, symbol: str):
        try:
            return mt5.symbol_info_tick(symbol)
        except Exception as e:
            logger.error(f"Error fetching tick data for {symbol}: {e}")
            return None

    def get_symbols(self):
        try:
            return mt5.symbols_get()
        except Exception as e:
            logger.error(f"Error fetching symbols: {e}")
            return []

    def connect(self):
        max_retries = 5
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                logger.info(f"Connecting to MT5 server {self.server or 'default'} as account {self.account or 'terminal-session'} (attempt {attempt + 1}/{max_retries})...")
                init_kwargs = {}
                if self.account and self.password and self.server:
                    init_kwargs = {
                        "login": int(self.account),
                        "password": self.password,
                        "server": self.server,
                    }
                if not mt5.initialize(**init_kwargs):
                    try:
                        last_error = mt5.last_error()
                    except Exception:
                        last_error = None
                    logger.error(f"MT5 initialization failed (attempt {attempt + 1}): {last_error}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 10)
                    continue

                account_info = mt5.account_info()
                if account_info is None:
                    logger.error(f"Failed to get account info (attempt {attempt + 1})")
                    mt5.shutdown()
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 10)
                    continue

                self.is_connected = True
                self._connection_retry_count = 0
                alert_manager.create(
                    "MT5 connected",
                    "MetaTrader 5 connection is active.",
                    severity="success",
                    category="execution",
                    event="mt5_reconnected",
                    dedupe_key="mt5_connected",
                    cooldown_seconds=60,
                )
                logger.info("Successfully connected to MT5")
                return True
            except Exception as e:
                logger.error(f"MT5 connection error (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 10)

        logger.critical(f"Failed to connect to MT5 after {max_retries} attempts")
        self.is_connected = False
        alert_manager.create(
            "Wrong MT5 credentials or terminal unavailable",
            "MT5 connection failed after retries. Check account, password, server, terminal login, and Algo Trading state.",
            severity="danger",
            category="execution",
            event="wrong_mt5_credentials",
            dedupe_key="wrong_mt5_credentials",
            cooldown_seconds=300,
        )
        return False

    def ensure_connected(self):
        if not self.is_connected:
            logger.warning("MT5 connection lost, attempting to reconnect...")
            alert_manager.create(
                "MT5 disconnected",
                "MT5 connection is down; attempting reconnect.",
                severity="danger",
                category="execution",
                event="mt5_disconnected",
                dedupe_key="mt5_disconnected",
                cooldown_seconds=60,
            )
            reconnected = self.connect()
            if reconnected:
                alert_manager.create(
                    "MT5 reconnected",
                    "MT5 connection recovered.",
                    severity="success",
                    category="execution",
                    event="mt5_reconnected",
                    dedupe_key="mt5_reconnected",
                    cooldown_seconds=60,
                )
            return reconnected
        return True

    def disconnect(self):
        try:
            if self.is_connected:
                mt5.shutdown()
                self.is_connected = False
                logger.info("Disconnected from MT5")
                alert_manager.create(
                    "MT5 disconnected",
                    "MT5 connection closed.",
                    severity="info",
                    category="execution",
                    event="mt5_disconnected",
                    dedupe_key="mt5_manual_disconnect",
                    cooldown_seconds=10,
                )
        except Exception as e:
            logger.error(f"Error disconnecting MT5: {e}")

    def get_account_info(self):
        try:
            if not self.is_connected:
                return None
            info = mt5.account_info()
            if info is None:
                return None
            return {
                "balance": info.balance,
                "equity": info.equity,
                "free_margin": info.margin_free,
                "margin_level": info.margin_level,
                "currency": info.currency,
            }
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return None

    def get_positions(self):
        try:
            if not self.is_connected:
                return []
            positions = mt5.positions_get()
            if positions is None:
                return []
            return [
                {
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "type": "BUY" if p.type == self._mt5_const("ORDER_TYPE_BUY", 0) else "SELL",
                    "volume": p.volume,
                    "entry": p.price_open,
                    "current": p.price_current,
                    "profit": p.profit,
                    "sl": getattr(p, "sl", None),
                    "tp": getattr(p, "tp", None),
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []

    def _market_order(self, symbol, volume, price, sl, tp, order_type, comment, label):
        self.last_order_error = None
        if not self.is_connected:
            self.last_order_error = "MT5 not connected"
            logger.error(self.last_order_error)
            return None

        request = {
            "action": self._mt5_const("TRADE_ACTION_DEAL", 1),
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "filling": self._get_filling_mode(symbol),
            "comment": comment,
        }
        result = self._safe_order_send(request, f"{label} order failed for {symbol}")
        if result is None:
            return None
        if self._retcode_done(result.retcode):
            logger.info(f"{label} order placed: {symbol} (vol={volume}, price={price:.5f}, SL={sl:.5f}, TP={tp:.5f})")
            return result.order

        error_msg = self._trade_error_message(result)
        self.last_order_error = f"{label} order failed for {symbol}: [{result.retcode}] {error_msg}"
        logger.error(self.last_order_error)
        return None

    def place_buy_order(self, symbol, volume, price, sl, tp):
        try:
            return self._market_order(
                symbol, volume, price, sl, tp,
                self._mt5_const("ORDER_TYPE_BUY", 0),
                "FVG_BUY",
                "BUY",
            )
        except Exception as e:
            self.last_order_error = f"Exception placing buy order for {symbol}: {e}"
            logger.error(self.last_order_error, exc_info=True)
            return None

    def place_sell_order(self, symbol, volume, price, sl, tp):
        try:
            return self._market_order(
                symbol, volume, price, sl, tp,
                self._mt5_const("ORDER_TYPE_SELL", 1),
                "FVG_SELL",
                "SELL",
            )
        except Exception as e:
            self.last_order_error = f"Exception placing sell order for {symbol}: {e}"
            logger.error(self.last_order_error, exc_info=True)
            return None

    def _pending_order(self, symbol, volume, price, sl, tp, order_type, comment, label):
        self.last_order_error = None
        if not self.is_connected:
            self.last_order_error = "MT5 not connected"
            return None
        request = {
            "action": self._mt5_const("TRADE_ACTION_PENDING", 5),
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "filling": self._get_filling_mode(symbol),
            "comment": comment,
        }
        result = self._safe_order_send(request, f"{label} order failed for {symbol}")
        if result is None:
            return None
        if self._retcode_placed_or_done(result.retcode):
            logger.info(f"{label} pending order placed: {symbol} at {price:.5f}")
            return result.order

        error_msg = self._trade_error_message(result)
        self.last_order_error = f"{label} order failed for {symbol}: [{result.retcode}] {error_msg}"
        logger.error(self.last_order_error)
        return None

    def place_buy_limit_order(self, symbol, volume, price, sl, tp):
        return self._pending_order(
            symbol, volume, price, sl, tp,
            self._mt5_const("ORDER_TYPE_BUY_LIMIT", 2),
            "PENDING_BUY_LIMIT_FVG",
            "BUY_LIMIT",
        )

    def place_sell_limit_order(self, symbol, volume, price, sl, tp):
        return self._pending_order(
            symbol, volume, price, sl, tp,
            self._mt5_const("ORDER_TYPE_SELL_LIMIT", 3),
            "PENDING_SELL_LIMIT_FVG",
            "SELL_LIMIT",
        )

    def get_pending_orders(self):
        try:
            if not self.is_connected:
                return []
            orders = mt5.orders_get()
            if orders is None:
                return []
            pending = []
            for order in orders:
                volume = getattr(order, "volume", None)
                if volume is None:
                    volume = getattr(order, "volume_current", None)
                if volume is None:
                    volume = getattr(order, "volume_initial", None)
                pending.append({
                    "ticket": order.ticket,
                    "symbol": order.symbol,
                    "type": self._order_type_to_string(order.type),
                    "volume": volume,
                    "price": order.price_open,
                    "sl": order.sl,
                    "tp": order.tp,
                    "time_setup": order.time_setup,
                    "comment": order.comment,
                })
            return pending
        except Exception as e:
            logger.error(f"Error getting pending orders: {e}")
            return []

    def cancel_order(self, ticket: int) -> bool:
        try:
            if not self.is_connected:
                return False
            result = self._safe_order_send(
                {"action": self._mt5_const("TRADE_ACTION_REMOVE", 8), "order": ticket},
                f"Cancel order failed for {ticket}",
            )
            if result is None:
                return False
            if self._retcode_done(result.retcode):
                logger.info(f"Pending order {ticket} cancelled")
                return True
            logger.error(f"Failed to cancel order {ticket}: {getattr(result, 'comment', result.retcode)}")
            return False
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False

    def _order_type_to_string(self, order_type):
        order_types = {
            self._mt5_const("ORDER_TYPE_BUY", 0): "BUY",
            self._mt5_const("ORDER_TYPE_SELL", 1): "SELL",
            self._mt5_const("ORDER_TYPE_BUY_LIMIT", 2): "BUY_LIMIT",
            self._mt5_const("ORDER_TYPE_SELL_LIMIT", 3): "SELL_LIMIT",
            self._mt5_const("ORDER_TYPE_BUY_STOP", 4): "BUY_STOP",
            self._mt5_const("ORDER_TYPE_SELL_STOP", 5): "SELL_STOP",
        }
        return order_types.get(order_type, "UNKNOWN")

    def modify_position_sltp(self, ticket, symbol, sl=None, tp=None):
        try:
            request = {
                "action": self._mt5_const("TRADE_ACTION_SLTP", 6),
                "position": ticket,
                "symbol": symbol,
            }
            if sl is not None:
                request["sl"] = sl
            if tp is not None:
                request["tp"] = tp

            result = self._safe_order_send(request, f"Modify SL/TP failed for position {ticket}")
            if result is None:
                return False
            if self._retcode_done(result.retcode):
                logger.info(f"Updated SL/TP for position {ticket}: SL={sl}, TP={tp}")
                return True
            logger.error(f"Failed to update SL/TP: {getattr(result, 'comment', result.retcode)}")
            return False
        except Exception as e:
            logger.error(f"Error modifying SL/TP: {e}")
            return False

    def modify_position_sl(self, ticket, symbol, sl):
        return self.modify_position_sltp(ticket, symbol, sl=sl)

    def modify_position_tp(self, ticket, symbol, tp):
        return self.modify_position_sltp(ticket, symbol, tp=tp)

    def _normalize_close_volume(self, symbol, requested_volume, current_volume):
        try:
            info = mt5.symbol_info(symbol)
            min_lot = float(getattr(info, "volume_min", 0.01) or 0.01)
            step = float(getattr(info, "volume_step", 0.01) or 0.01)
            current_volume = float(current_volume or 0)
            requested_volume = min(float(requested_volume or 0), current_volume)
            if requested_volume <= 0:
                return 0
            steps = int(requested_volume / step)
            volume = round(steps * step, 2)
            if volume < min_lot and current_volume >= min_lot:
                volume = min_lot
            if current_volume - volume > 0 and current_volume - volume < min_lot:
                volume = current_volume
            return round(min(volume, current_volume), 2)
        except Exception:
            return round(min(float(requested_volume or 0), float(current_volume or 0)), 2)

    def close_position_volume(self, ticket, volume=None, comment="PARTIAL_TP"):
        try:
            if not self.ensure_connected():
                logger.error("MT5 not connected")
                return False

            positions = mt5.positions_get()
            if not positions:
                return False
            for pos in positions:
                if pos.ticket != ticket:
                    continue

                close_volume = pos.volume if volume is None else self._normalize_close_volume(pos.symbol, volume, pos.volume)
                if close_volume <= 0:
                    logger.error(f"Invalid close volume for position {ticket}: {close_volume}")
                    return False

                tick = mt5.symbol_info_tick(pos.symbol)
                if tick is None:
                    logger.error(f"Failed to get tick for closing position {ticket} ({pos.symbol})")
                    return False

                close_type = (
                    self._mt5_const("ORDER_TYPE_SELL", 1)
                    if pos.type == self._mt5_const("ORDER_TYPE_BUY", 0)
                    else self._mt5_const("ORDER_TYPE_BUY", 0)
                )
                price = tick.bid if close_type == self._mt5_const("ORDER_TYPE_SELL", 1) else tick.ask
                request = {
                    "action": self._mt5_const("TRADE_ACTION_DEAL", 1),
                    "symbol": pos.symbol,
                    "volume": close_volume,
                    "type": close_type,
                    "position": ticket,
                    "price": price,
                    "deviation": 20,
                    "filling": self._get_filling_mode(pos.symbol),
                    "comment": comment,
                }
                result = self._safe_order_send(request, f"Close position failed for {ticket}")
                if result is None:
                    return False
                if self._retcode_done(result.retcode):
                    logger.info(f"Closed {close_volume} lots from position {ticket}")
                    return True
                logger.error(f"Failed to close position {ticket}: [{result.retcode}] {getattr(result, 'comment', '')}")
                return False
            return False
        except Exception as e:
            logger.error(f"Error partially closing position: {e}")
            return False

    def close_position(self, ticket):
        try:
            return self.close_position_volume(ticket, volume=None, comment="PANIC_CLOSE")
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return False
