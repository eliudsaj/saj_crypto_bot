"""Binance Spot broker adapter.

Live order routing is intentionally opt-in. By default this adapter can test
credentials and simulate orders for paper/testnet workflows without sending
real orders to Binance.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from types import SimpleNamespace
from typing import Any

from .base import BrokerAdapter

logger = logging.getLogger(__name__)


class BinanceBrokerAdapter(BrokerAdapter):
    broker_type = "binance"
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 60
    ORDER_TYPE_BUY_LIMIT = "BUY_LIMIT"
    ORDER_TYPE_SELL_LIMIT = "SELL_LIMIT"

    def __init__(self, profile: dict[str, Any] | None = None):
        self.profile = profile or {}
        self.metadata = self.profile.get("metadata") if isinstance(self.profile.get("metadata"), dict) else {}
        self.api_key = str(self.profile.get("account") or os.getenv("BINANCE_API_KEY", "")).strip()
        self.api_secret = str(self.profile.get("password") or os.getenv("BINANCE_API_SECRET", "")).strip()
        self.market = str(self.metadata.get("market") or os.getenv("BINANCE_MARKET", "spot")).lower()
        self.testnet = self._bool(self.metadata.get("testnet"), os.getenv("BINANCE_TESTNET", "true"))
        self.paper = self._bool(self.metadata.get("paper"), os.getenv("BINANCE_PAPER_TRADING", "true"))
        self.live_enabled = self._bool(
            self.metadata.get("live_trading_enabled"),
            os.getenv("BINANCE_LIVE_TRADING_ENABLED", "false"),
        )
        self.live_ack = str(
            self.metadata.get("live_ack")
            or os.getenv("BINANCE_LIVE_ACK", "")
        ).strip()
        self.recv_window = int(self.metadata.get("recv_window") or os.getenv("BINANCE_RECV_WINDOW", "5000"))
        self.timeout = float(self.metadata.get("timeout") or os.getenv("BINANCE_TIMEOUT_SECONDS", "10"))
        self.paper_balance = float(self.metadata.get("paper_balance") or os.getenv("BINANCE_PAPER_BALANCE", "10000"))
        self.base_url = self._base_url()
        self.is_connected = False
        self.last_order_error = None
        self._time_offset_ms = 0
        self._time_synced = False
        self._paper_order_id = int(time.time() * 1000)
        self._exchange_info_cache: dict[str, Any] = {}

    def _bool(self, value, default=False) -> bool:
        if value is None:
            value = default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _base_url(self) -> str:
        configured = str(self.profile.get("server") or "").strip().rstrip("/")
        if configured:
            if not configured.startswith(("https://", "http://")):
                raise ValueError("Binance server must be a full API URL or blank. Leave it blank for the default Binance endpoint.")
            return configured
        if self.market != "spot":
            raise NotImplementedError("Only Binance Spot is implemented. Futures/cTrader/Bybit remain adapter-ready.")
        return "https://testnet.binance.vision" if self.testnet else "https://api.binance.com"

    def live_ready(self) -> bool:
        return (
            self.live_enabled
            and not self.paper
            and not self.testnet
            and self.live_ack == "I_UNDERSTAND_BINANCE_LIVE_RISK"
        )

    def live_block_reason(self) -> str:
        if self.testnet:
            return "Binance live blocked: profile is still using testnet"
        if self.paper:
            return "Binance live blocked: paper/simulated orders are still enabled"
        if not self.live_enabled:
            return "Binance live blocked: live trading is not enabled on this profile"
        if self.live_ack != "I_UNDERSTAND_BINANCE_LIVE_RISK":
            return "Binance live blocked: BINANCE_LIVE_ACK is missing or incorrect"
        return ""

    def _headers(self, signed: bool = False) -> dict[str, str]:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if signed:
            headers["X-MBX-APIKEY"] = self.api_key
        return headers

    def _encode(self, params: dict[str, Any]) -> str:
        clean = {k: v for k, v in params.items() if v is not None}
        return urllib.parse.urlencode(clean)

    def _sign(self, query: str) -> str:
        return hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()

    def _sync_server_time(self) -> None:
        try:
            url = f"{self.base_url}/api/v3/time"
            request = urllib.request.Request(url, method="GET", headers=self._headers())
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            server_time = int(data.get("serverTime") or 0)
            if server_time:
                self._time_offset_ms = server_time - int(time.time() * 1000)
                self._time_synced = True
        except Exception as exc:
            logger.warning("Binance server time sync failed: %s", exc)

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None, signed: bool = False, _retry_time_sync: bool = True):
        original_params = dict(params or {})
        params = dict(original_params)
        body = None
        query = ""
        if signed:
            if not self.api_key or not self.api_secret:
                raise ValueError("Binance API key and secret are required for signed endpoints")
            if not self._time_synced:
                self._sync_server_time()
            params.setdefault("recvWindow", self.recv_window)
            params["timestamp"] = int(time.time() * 1000) + self._time_offset_ms
            query = self._encode(params)
            params["signature"] = self._sign(query)
        query = self._encode(params)
        url = f"{self.base_url}{path}"
        method = method.upper()
        if method in {"GET", "DELETE"} and query:
            url = f"{url}?{query}"
        elif method in {"POST", "PUT"}:
            body = query.encode("utf-8")

        request = urllib.request.Request(url, data=body, method=method, headers=self._headers(signed=signed))
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self.last_order_error = f"Binance HTTP {exc.code}: {detail}"
            logger.error(self.last_order_error)
            if signed and _retry_time_sync and '"code":-1021' in detail:
                self._sync_server_time()
                return self._request(method, path, original_params, signed=signed, _retry_time_sync=False)
            raise
        except Exception as exc:
            self.last_order_error = f"Binance request failed: {exc}"
            logger.error(self.last_order_error)
            raise

    def connect(self) -> bool:
        try:
            if self.paper:
                self.is_connected = True
                return True
            self.get_account_info()
            self.is_connected = True
            return True
        except Exception:
            self.is_connected = False
            return False

    def ensure_connected(self) -> bool:
        return self.is_connected or self.connect()

    def disconnect(self) -> None:
        self.is_connected = False

    def get_account_info(self) -> dict[str, Any] | None:
        if self.paper:
            return {
                "balance": self.paper_balance,
                "equity": self.paper_balance,
                "free_margin": self.paper_balance,
                "margin_level": None,
                "currency": "USDT",
                "account_type": "SPOT_PAPER",
                "balances": [],
            }
        data = self._request("GET", "/api/v3/account", signed=True)
        balances = data.get("balances") or []
        usdt = next((item for item in balances if item.get("asset") == "USDT"), {})
        free = float(usdt.get("free") or 0)
        locked = float(usdt.get("locked") or 0)
        return {
            "balance": free + locked,
            "equity": free + locked,
            "free_margin": free,
            "margin_level": None,
            "currency": "USDT",
            "account_type": data.get("accountType", "SPOT"),
            "can_trade": data.get("canTrade"),
            "balances": balances,
        }

    def get_positions(self) -> list[dict[str, Any]]:
        # Spot does not expose directional leveraged positions.
        return []

    def get_symbol_info(self, symbol: str) -> Any:
        symbol = self._normalize_symbol(symbol)
        info = self._get_exchange_symbol(symbol)
        tick_size = self._filter_value(info, "PRICE_FILTER", "tickSize") or "0.00000001"
        step_size = self._filter_value(info, "LOT_SIZE", "stepSize") or "0.00000001"
        digits = self._decimal_places(tick_size)
        return SimpleNamespace(
            name=symbol,
            symbol=symbol,
            digits=digits,
            point=float(tick_size),
            trade_tick_size=float(tick_size),
            volume_step=float(step_size),
            volume_min=float(self._filter_value(info, "LOT_SIZE", "minQty") or step_size),
            raw=info,
        )

    def get_symbol_tick(self, symbol: str) -> Any:
        symbol = self._normalize_symbol(symbol)
        data = self._request("GET", "/api/v3/ticker/bookTicker", {"symbol": symbol})
        return SimpleNamespace(
            symbol=symbol,
            bid=float(data.get("bidPrice") or 0),
            ask=float(data.get("askPrice") or 0),
        )

    def get_symbols(self):
        data = self._request("GET", "/api/v3/exchangeInfo")
        return [SimpleNamespace(name=item.get("symbol"), symbol=item.get("symbol"), visible=True) for item in data.get("symbols", [])]

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start_pos: int, count: int):
        interval = self._timeframe_to_interval(timeframe)
        limit = max(1, min(1000, int(count or 100) + int(start_pos or 0)))
        data = self._request("GET", "/api/v3/klines", {"symbol": self._normalize_symbol(symbol), "interval": interval, "limit": limit})
        rows = data[int(start_pos or 0):]
        return [
            {
                "time": int(item[0] / 1000),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "tick_volume": float(item[5]),
                "spread": 0,
                "real_volume": float(item[5]),
            }
            for item in rows[:count]
        ]

    def place_order(self, **kwargs):
        symbol = self._normalize_symbol(kwargs.get("symbol"))
        side = str(kwargs.get("action") or kwargs.get("direction") or "").upper()
        order_type = str(kwargs.get("order_type") or "market").lower()
        quantity = kwargs.get("quantity") or kwargs.get("volume")
        price = kwargs.get("price")
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"Unsupported Binance order side: {side}")
        if not quantity:
            raise ValueError("Binance order quantity is required")
        if not self.live_ready():
            self._paper_order_id += 1
            logger.info(
                "Paper Binance %s %s order simulated for %s qty=%s (%s)",
                side,
                order_type,
                symbol,
                quantity,
                self.live_block_reason() or "live routing disabled",
            )
            return self._paper_order_id

        payload = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET" if order_type == "market" else "LIMIT",
            "quantity": self._format_decimal(quantity),
            "newOrderRespType": "RESULT",
        }
        if payload["type"] == "LIMIT":
            payload["timeInForce"] = "GTC"
            payload["price"] = self._format_decimal(price)
        result = self._request("POST", "/api/v3/order", payload, signed=True)
        return result.get("orderId")

    def modify_order(self, order_id: int, **kwargs) -> bool:
        self.last_order_error = "Binance Spot order modification is not implemented; cancel and replace instead"
        logger.warning(self.last_order_error)
        return False

    def close_order(self, order_id: int, **kwargs) -> bool:
        return self.cancel_order(order_id, symbol=kwargs.get("symbol"))

    def cancel_order(self, ticket: int, symbol: str | None = None) -> bool:
        if not symbol:
            self.last_order_error = "Binance cancel_order requires a symbol"
            return False
        if not self.live_ready():
            return True
        self._request("DELETE", "/api/v3/order", {"symbol": self._normalize_symbol(symbol), "orderId": ticket}, signed=True)
        return True

    def place_buy_order(self, symbol, volume, price=None, sl=None, tp=None):
        return self.place_order(symbol=symbol, action="BUY", volume=volume, order_type="market", price=price)

    def place_sell_order(self, symbol, volume, price=None, sl=None, tp=None):
        return self.place_order(symbol=symbol, action="SELL", volume=volume, order_type="market", price=price)

    def place_buy_limit_order(self, symbol, volume, price, sl=None, tp=None):
        return self.place_order(symbol=symbol, action="BUY", volume=volume, order_type="limit", price=price)

    def place_sell_limit_order(self, symbol, volume, price, sl=None, tp=None):
        return self.place_order(symbol=symbol, action="SELL", volume=volume, order_type="limit", price=price)

    def modify_position_sl(self, ticket, symbol, sl):
        return False

    def modify_position_tp(self, ticket, symbol, tp):
        return False

    def close_position_volume(self, ticket, volume=None, comment="BINANCE_CLOSE"):
        return False

    def get_pending_orders(self) -> list[dict[str, Any]]:
        if self.paper:
            return []
        try:
            orders = self._request("GET", "/api/v3/openOrders", signed=True)
        except Exception:
            return []
        return [
            {
                "ticket": item.get("orderId"),
                "symbol": item.get("symbol"),
                "type": item.get("side"),
                "volume": float(item.get("origQty") or 0),
                "price": float(item.get("price") or 0),
                "status": item.get("status"),
                "comment": item.get("clientOrderId"),
            }
            for item in orders
        ]

    def orders_get(self, **kwargs):
        return []

    def _normalize_symbol(self, symbol) -> str:
        return str(symbol or "").replace("/", "").replace("-", "").upper()

    def _get_exchange_symbol(self, symbol: str) -> dict[str, Any]:
        if symbol in self._exchange_info_cache:
            return self._exchange_info_cache[symbol]
        data = self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})
        item = (data.get("symbols") or [{}])[0]
        self._exchange_info_cache[symbol] = item
        return item

    def _filter_value(self, symbol_info: dict[str, Any], filter_type: str, key: str):
        for item in symbol_info.get("filters", []):
            if item.get("filterType") == filter_type:
                return item.get(key)
        return None

    def _decimal_places(self, value: str) -> int:
        text = str(value).rstrip("0")
        return len(text.split(".", 1)[1]) if "." in text else 0

    def _format_decimal(self, value) -> str:
        return str(value).rstrip("0").rstrip(".") if "." in str(value) else str(value)

    def _timeframe_to_interval(self, timeframe: int) -> str:
        mapping = {
            1: "1m",
            5: "5m",
            15: "15m",
            30: "30m",
            60: "1h",
            240: "4h",
            1440: "1d",
        }
        return mapping.get(int(timeframe or 5), "5m")
