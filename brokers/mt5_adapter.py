"""MT5 broker adapter implementation."""

from __future__ import annotations

import logging
from typing import Any

import MetaTrader5 as mt5

from .base import BrokerAdapter
from mt5_interface import MT5Interface

logger = logging.getLogger(__name__)


class MT5BrokerAdapter(BrokerAdapter):
    """BrokerAdapter wrapper around the existing MT5Interface."""

    broker_type = "mt5"

    TIMEFRAME_M30 = mt5.TIMEFRAME_M30
    ORDER_TYPE_BUY_LIMIT = mt5.ORDER_TYPE_BUY_LIMIT
    ORDER_TYPE_SELL_LIMIT = mt5.ORDER_TYPE_SELL_LIMIT

    def __init__(self, profile: dict[str, Any] | None = None, interface: MT5Interface | None = None):
        self.profile = profile or {}
        self.interface = interface or MT5Interface()
        self.name = self.profile.get("name") or "MetaTrader 5"
        self.profile_id = self.profile.get("id")
        if self.profile.get("account"):
            self.interface.account = str(self.profile.get("account"))
        if self.profile.get("password"):
            self.interface.password = str(self.profile.get("password"))
        if self.profile.get("server"):
            self.interface.server = str(self.profile.get("server"))

    @property
    def is_connected(self) -> bool:
        return bool(getattr(self.interface, "is_connected", False))

    @property
    def last_order_error(self):
        return getattr(self.interface, "last_order_error", None)

    def connect(self) -> bool:
        return bool(self.interface.connect())

    def ensure_connected(self) -> bool:
        return bool(self.interface.ensure_connected())

    def disconnect(self) -> None:
        self.interface.disconnect()

    def get_account_info(self) -> dict[str, Any] | None:
        return self.interface.get_account_info()

    def get_positions(self) -> list[dict[str, Any]]:
        return self.interface.get_positions()

    def get_symbol_info(self, symbol: str) -> Any:
        return self.interface.get_symbol_info(symbol)

    def get_symbol_tick(self, symbol: str) -> Any:
        return self.interface.get_symbol_tick(symbol)

    def get_symbols(self) -> Any:
        return self.interface.get_symbols()

    def get_pending_orders(self) -> list[dict[str, Any]]:
        return self.interface.get_pending_orders()

    def orders_get(self, **kwargs):
        try:
            return mt5.orders_get(**kwargs)
        except Exception as exc:
            logger.error("Error fetching MT5 pending orders through adapter: %s", exc)
            return None

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start_pos: int, count: int):
        try:
            return mt5.copy_rates_from_pos(symbol, timeframe, start_pos, count)
        except Exception as exc:
            logger.error("Error fetching MT5 rates through adapter for %s: %s", symbol, exc)
            return None

    def place_order(self, **kwargs):
        symbol = kwargs.get("symbol")
        action = str(kwargs.get("action") or kwargs.get("direction") or "").upper()
        order_type = str(kwargs.get("order_type") or "market").lower()
        volume = kwargs.get("volume")
        price = kwargs.get("price")
        sl = kwargs.get("sl")
        tp = kwargs.get("tp")
        if action == "BUY" and order_type in {"limit", "buy_limit"}:
            return self.place_buy_limit_order(symbol, volume, price, sl, tp)
        if action == "SELL" and order_type in {"limit", "sell_limit"}:
            return self.place_sell_limit_order(symbol, volume, price, sl, tp)
        if action == "BUY":
            return self.place_buy_order(symbol, volume, price, sl, tp)
        if action == "SELL":
            return self.place_sell_order(symbol, volume, price, sl, tp)
        raise ValueError(f"Unsupported MT5 order action: {action}")

    def modify_order(self, order_id: int, **kwargs) -> bool:
        symbol = kwargs.get("symbol")
        sl = kwargs.get("sl")
        tp = kwargs.get("tp")
        if not symbol:
            logger.error("modify_order requires symbol for MT5 positions")
            return False
        return bool(self.interface.modify_position_sltp(order_id, symbol, sl=sl, tp=tp))

    def close_order(self, order_id: int, **kwargs) -> bool:
        volume = kwargs.get("volume")
        comment = kwargs.get("comment", "BROKER_CLOSE")
        return bool(self.interface.close_position_volume(order_id, volume=volume, comment=comment))

    def cancel_order(self, ticket: int) -> bool:
        return bool(self.interface.cancel_order(ticket))

    def place_buy_order(self, symbol, volume, price, sl, tp):
        return self.interface.place_buy_order(symbol, volume, price, sl, tp)

    def place_sell_order(self, symbol, volume, price, sl, tp):
        return self.interface.place_sell_order(symbol, volume, price, sl, tp)

    def place_buy_limit_order(self, symbol, volume, price, sl, tp):
        return self.interface.place_buy_limit_order(symbol, volume, price, sl, tp)

    def place_sell_limit_order(self, symbol, volume, price, sl, tp):
        return self.interface.place_sell_limit_order(symbol, volume, price, sl, tp)

    def modify_position_sltp(self, ticket, symbol, sl=None, tp=None):
        return self.interface.modify_position_sltp(ticket, symbol, sl=sl, tp=tp)

    def modify_position_sl(self, ticket, symbol, sl):
        return self.interface.modify_position_sl(ticket, symbol, sl)

    def modify_position_tp(self, ticket, symbol, tp):
        return self.interface.modify_position_tp(ticket, symbol, tp)

    def close_position_volume(self, ticket, volume=None, comment="BROKER_CLOSE"):
        return self.interface.close_position_volume(ticket, volume=volume, comment=comment)

    def close_position(self, ticket):
        return self.interface.close_position(ticket)

    def send_raw_order(self, request: dict[str, Any], context: str = "Broker raw order"):
        return self.interface._safe_order_send(request, context)

    def attach_order_metadata(self, order_id, metadata):
        if hasattr(self.interface, "attach_order_metadata"):
            return self.interface.attach_order_metadata(order_id, metadata)
        return None

    def get_order_metadata(self, order_id):
        if hasattr(self.interface, "get_order_metadata"):
            return self.interface.get_order_metadata(order_id)
        return None

    def get_profile_summary(self) -> dict[str, Any]:
        return {
            "id": self.profile_id,
            "name": self.name,
            "broker_type": self.broker_type,
            "account": self.profile.get("account"),
            "server": self.profile.get("server"),
            "connected": self.is_connected,
        }

    def __getattr__(self, item):
        return getattr(self.interface, item)
