"""Historical replay backtesting for Nexus Trading Bot.

The replay runner deliberately drives the existing TradingEngine instead of
duplicating strategy rules. Historical candles are exposed through a small MT5
compatibility shim so scanner, War Room, execution gates, and trade management
logic read the same APIs they use in live trading.
"""

from __future__ import annotations

import argparse
import csv
import html
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

import MetaTrader5 as mt5
import pandas as pd

from analytics.performance import summarize_performance
from analytics.ict_diagnostics import build_summary, reset_reports
from engine import TradingEngine

logger = logging.getLogger(__name__)


TIMEFRAME_MAP = {
    "M1": getattr(mt5, "TIMEFRAME_M1", 1),
    "M5": getattr(mt5, "TIMEFRAME_M5", 5),
    "M15": getattr(mt5, "TIMEFRAME_M15", 15),
    "M30": getattr(mt5, "TIMEFRAME_M30", 30),
    "H1": getattr(mt5, "TIMEFRAME_H1", 60),
    "H4": getattr(mt5, "TIMEFRAME_H4", 240),
}


@contextmanager
def _temporary_env(values: dict[str, str]):
    originals = {key: os.environ.get(key) for key in values}
    for key, value in values.items():
        os.environ[key] = str(value)
    try:
        yield
    finally:
        for key, value in originals.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@dataclass
class ReplayPosition:
    ticket: int
    symbol: str
    type: str
    volume: float
    entry: float
    sl: float
    tp: float
    opened_at: str
    initial_volume: float
    initial_risk_price: float
    realized_profit: float = 0.0
    max_favorable_r: float = 0.0
    mae_r: float = 0.0
    mfe_r: float = 0.0
    metadata: dict | None = None
    opened_index: int | None = None


class ReplayBroker:
    """In-memory broker that implements the MT5Interface methods used by engine."""

    def __init__(self, initial_balance: float = 10000.0):
        self.initial_balance = float(initial_balance)
        self.balance = float(initial_balance)
        self.equity = float(initial_balance)
        self.positions: dict[int, ReplayPosition] = {}
        self.pending_orders: dict[int, dict] = {}
        self.closed_trades: list[dict] = []
        self.partial_events: list[dict] = []
        self.last_order_error = None
        self.is_connected = True
        self.current_bar_by_symbol: dict[str, dict] = {}
        self.current_time = None
        self._ticket = 100000
        self.order_metadata: dict[int, dict] = {}
        self.bar_history_by_symbol: dict[str, list[dict]] = {}
        self.cursor_by_symbol: dict[str, int] = {}

    def _next_ticket(self) -> int:
        self._ticket += 1
        return self._ticket

    def _pip_size(self, symbol: str) -> float:
        return 0.01 if str(symbol).upper().endswith("JPY") else 0.0001

    def _pip_value(self, symbol: str) -> float:
        return 10.0

    def _profit(self, position: ReplayPosition, price: float | None = None) -> float:
        bar = self.current_bar_by_symbol.get(position.symbol, {})
        current = float(price if price is not None else bar.get("close", position.entry))
        pip_size = self._pip_size(position.symbol)
        move = current - position.entry if position.type == "BUY" else position.entry - current
        return (move / pip_size) * self._pip_value(position.symbol) * position.volume + position.realized_profit

    def _position_to_dict(self, position: ReplayPosition) -> dict:
        bar = self.current_bar_by_symbol.get(position.symbol, {})
        current = float(bar.get("close", position.entry))
        return {
            "ticket": position.ticket,
            "symbol": position.symbol,
            "type": position.type,
            "volume": position.volume,
            "entry": position.entry,
            "current": current,
            "profit": self._profit(position, current),
            "sl": position.sl,
            "tp": position.tp,
        }

    def update_bar(self, symbol: str, bar: dict):
        self.current_bar_by_symbol[symbol] = bar
        self.bar_history_by_symbol.setdefault(symbol, []).append(dict(bar))
        self.cursor_by_symbol[symbol] = len(self.bar_history_by_symbol[symbol]) - 1
        self.current_time = datetime.fromtimestamp(int(bar["time"]))
        self._fill_pending_orders(symbol, bar)
        self._update_excursions(symbol, bar)
        self._settle_stops_and_targets(symbol, bar)
        self._mark_to_market()

    def _fill_pending_orders(self, symbol: str, bar: dict):
        for ticket, order in list(self.pending_orders.items()):
            if order["symbol"] != symbol:
                continue
            entry = float(order["entry"])
            action = order["action"]
            should_fill = (
                (action == "BUY" and float(bar["low"]) <= entry)
                or (action == "SELL" and float(bar["high"]) >= entry)
            )
            if should_fill:
                self._open_position(ticket, symbol, action, order["volume"], entry, order["sl"], order["tp"], order.get("metadata"))
                self.pending_orders.pop(ticket, None)

    def _settle_stops_and_targets(self, symbol: str, bar: dict):
        for ticket, position in list(self.positions.items()):
            if position.symbol != symbol:
                continue

            high = float(bar["high"])
            low = float(bar["low"])
            exit_price = None
            reason = None

            if position.type == "BUY":
                if low <= position.sl:
                    exit_price = position.sl
                    reason = "SL"
                elif high >= position.tp:
                    exit_price = position.tp
                    reason = "TP"
            else:
                if high >= position.sl:
                    exit_price = position.sl
                    reason = "SL"
                elif low <= position.tp:
                    exit_price = position.tp
                    reason = "TP"

            if exit_price is not None:
                self._close_position(position, exit_price, reason)

    def _mark_to_market(self):
        floating = sum(self._profit(position) - position.realized_profit for position in self.positions.values())
        self.equity = self.balance + floating

    def _open_position(self, ticket: int, symbol: str, action: str, volume: float, entry: float, sl: float, tp: float, metadata: dict | None = None):
        risk_price = abs(float(entry) - float(sl))
        self.positions[ticket] = ReplayPosition(
            ticket=ticket,
            symbol=symbol,
            type=action,
            volume=float(volume),
            entry=float(entry),
            sl=float(sl),
            tp=float(tp),
            opened_at=self.current_time.isoformat() if self.current_time else datetime.now().isoformat(),
            initial_volume=float(volume),
            initial_risk_price=risk_price,
            metadata=dict(metadata or self.order_metadata.get(ticket) or {}),
            opened_index=self.cursor_by_symbol.get(symbol),
        )

    def _close_position(self, position: ReplayPosition, exit_price: float, reason: str):
        profit = self._profit(position, exit_price)
        self.balance += profit - position.realized_profit
        metadata = dict(position.metadata or {})
        ict_components = metadata.get("ict_components") or {}
        entry_diagnostics = metadata.get("entry_diagnostics") or {}
        snapshot_path = None
        if self._r_multiple(position, profit) < 0:
            snapshot_path = self._write_loss_snapshot(position, float(exit_price), reason, metadata)
        self.closed_trades.append({
            "ticket": position.ticket,
            "symbol": position.symbol,
            "action": position.type,
            "strategy_type": metadata.get("strategy_type") or "current",
            "liquidity_sweep_detected": metadata.get("liquidity_sweep_detected"),
            "bos_detected": metadata.get("bos_detected"),
            "choch_detected": metadata.get("choch_detected"),
            "fvg_retest_detected": metadata.get("fvg_retest_detected"),
            "order_block_valid": metadata.get("order_block_valid"),
            "fvg_present": metadata.get("fvg_present"),
            "inside_premium_zone": metadata.get("inside_premium_zone"),
            "inside_discount_zone": metadata.get("inside_discount_zone"),
            "premium_discount_zone": metadata.get("premium_discount_zone"),
            "htf_bias_aligned": metadata.get("htf_bias_aligned"),
            "spread_pips": metadata.get("spread_pips"),
            "volatility_state": metadata.get("volatility_state"),
            "volatility_score": metadata.get("volatility_score"),
            "entry_diagnostics": entry_diagnostics,
            "ict_components": ict_components,
            "session_name": metadata.get("session_name"),
            "entry_reason": metadata.get("entry_reason"),
            "exit_reason": reason,
            "entry_time": position.opened_at,
            "exit_time": self.current_time.isoformat() if self.current_time else datetime.now().isoformat(),
            "entry": position.entry,
            "exit": float(exit_price),
            "sl": position.sl,
            "tp": position.tp,
            "volume": position.initial_volume,
            "remaining_volume": position.volume,
            "profit": profit,
            "reason": reason,
            "r_multiple": self._r_multiple(position, profit),
            "mae_r": position.mae_r,
            "mfe_r": position.mfe_r,
            "chart_snapshot": snapshot_path,
            "failure_explanation": self._failure_explanation(position, reason, metadata),
        })
        self.positions.pop(position.ticket, None)
        self.order_metadata.pop(position.ticket, None)
        self._mark_to_market()

    def _update_excursions(self, symbol: str, bar: dict):
        for position in self.positions.values():
            if position.symbol != symbol or position.initial_risk_price <= 0:
                continue
            high = float(bar["high"])
            low = float(bar["low"])
            if position.type == "BUY":
                favorable = (high - position.entry) / position.initial_risk_price
                adverse = (low - position.entry) / position.initial_risk_price
            else:
                favorable = (position.entry - low) / position.initial_risk_price
                adverse = (position.entry - high) / position.initial_risk_price
            position.mfe_r = max(position.mfe_r, float(favorable))
            position.mae_r = min(position.mae_r, float(adverse))

    def _failure_explanation(self, position: ReplayPosition, reason: str, metadata: dict) -> str:
        diagnostics = metadata.get("entry_diagnostics") or {}
        missing = []
        if not diagnostics.get("htf_bias_aligned"):
            missing.append("HTF bias was not aligned")
        if not diagnostics.get("liquidity_sweep"):
            missing.append("liquidity sweep was missing")
        if not (diagnostics.get("bos_confirmed") or diagnostics.get("choch_confirmed")):
            missing.append("BOS/CHoCH was not confirmed")
        if not diagnostics.get("fvg_present"):
            missing.append("valid FVG was missing")
        if not diagnostics.get("fvg_retested"):
            missing.append("FVG retest was missing")
        zone = diagnostics.get("premium_discount_zone")
        if position.type == "BUY" and zone == "premium":
            missing.append("BUY entry was in premium")
        if position.type == "SELL" and zone == "discount":
            missing.append("SELL entry was in discount")
        if missing:
            return f"{reason}: " + "; ".join(missing)
        return f"{reason}: entry met recorded diagnostics but did not follow through to target"

    def _write_loss_snapshot(self, position: ReplayPosition, exit_price: float, reason: str, metadata: dict) -> str | None:
        history = self.bar_history_by_symbol.get(position.symbol) or []
        if not history:
            return None
        opened = position.opened_index if position.opened_index is not None else len(history) - 1
        start = max(0, opened - 30)
        end = min(len(history), opened + 31)
        window = history[start:end]
        if not window:
            return None
        out_dir = os.path.join("data", "trade_snapshots")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{position.symbol}_{position.ticket}_{reason}.svg")
        _write_trade_snapshot_svg(path, window, position, opened - start, exit_price, metadata)
        return path

    def _r_multiple(self, position: ReplayPosition, profit: float) -> float:
        risk_cash = (position.initial_risk_price / self._pip_size(position.symbol)) * self._pip_value(position.symbol) * position.initial_volume
        return profit / risk_cash if risk_cash > 0 else 0.0

    def get_account_info(self):
        self._mark_to_market()
        return {
            "balance": self.balance,
            "equity": self.equity,
            "free_margin": self.equity,
            "margin_level": 1000.0,
            "currency": "USD",
        }

    def get_positions(self):
        return [self._position_to_dict(position) for position in self.positions.values()]

    def get_pending_orders(self):
        return [
            {
                "ticket": ticket,
                "symbol": order["symbol"],
                "type": order["action"],
                "volume": order["volume"],
                "entry": order["entry"],
                "sl": order["sl"],
                "tp": order["tp"],
                "status": "PENDING",
            }
            for ticket, order in self.pending_orders.items()
        ]

    def get_symbol_info(self, symbol: str):
        digits = 3 if str(symbol).upper().endswith("JPY") else 5
        return SimpleNamespace(
            name=symbol,
            digits=digits,
            point=10 ** -digits,
            trade_tick_value=1.0,
            trade_tick_size=10 ** -digits,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            filling_mode=getattr(mt5, "ORDER_FILLING_IOC", 1),
        )

    def get_symbol_tick(self, symbol: str):
        bar = self.current_bar_by_symbol.get(symbol, {})
        price = float(bar.get("close", 0.0))
        spread = self._pip_size(symbol) * 0.2
        return SimpleNamespace(bid=price - spread / 2, ask=price + spread / 2)

    def place_buy_order(self, symbol, volume, price, sl, tp):
        ticket = self._next_ticket()
        self._open_position(ticket, symbol, "BUY", volume, price, sl, tp)
        return ticket

    def place_sell_order(self, symbol, volume, price, sl, tp):
        ticket = self._next_ticket()
        self._open_position(ticket, symbol, "SELL", volume, price, sl, tp)
        return ticket

    def place_buy_limit_order(self, symbol, volume, price, sl, tp):
        ticket = self._next_ticket()
        self.pending_orders[ticket] = {"symbol": symbol, "action": "BUY", "volume": float(volume), "entry": float(price), "sl": float(sl), "tp": float(tp)}
        return ticket

    def place_sell_limit_order(self, symbol, volume, price, sl, tp):
        ticket = self._next_ticket()
        self.pending_orders[ticket] = {"symbol": symbol, "action": "SELL", "volume": float(volume), "entry": float(price), "sl": float(sl), "tp": float(tp)}
        return ticket

    def attach_order_metadata(self, ticket: int, metadata: dict):
        payload = dict(metadata or {})
        self.order_metadata[ticket] = payload
        if ticket in self.pending_orders:
            self.pending_orders[ticket]["metadata"] = payload
        if ticket in self.positions:
            self.positions[ticket].metadata = payload

    def modify_position_sl(self, ticket, symbol, new_sl):
        position = self.positions.get(ticket)
        if not position or position.symbol != symbol:
            return False
        position.sl = float(new_sl)
        return True

    def modify_position_tp(self, ticket, symbol, new_tp):
        position = self.positions.get(ticket)
        if not position or position.symbol != symbol:
            return False
        position.tp = float(new_tp)
        return True

    def close_position_volume(self, ticket, volume=None, comment="BACKTEST_CLOSE"):
        position = self.positions.get(ticket)
        if not position:
            return False
        close_volume = position.volume if volume is None else min(position.volume, float(volume))
        if close_volume <= 0:
            return False

        bar = self.current_bar_by_symbol.get(position.symbol, {})
        exit_price = float(bar.get("close", position.entry))
        if close_volume >= position.volume:
            self._close_position(position, exit_price, comment)
            return True

        fraction = close_volume / position.volume
        gross_profit = self._profit(position, exit_price) - position.realized_profit
        realized = gross_profit * fraction
        self.balance += realized
        position.realized_profit += realized
        position.volume = round(position.volume - close_volume, 8)
        self.partial_events.append({
            "ticket": ticket,
            "symbol": position.symbol,
            "time": self.current_time.isoformat() if self.current_time else datetime.now().isoformat(),
            "action": position.type,
            "exit": exit_price,
            "closed_volume": close_volume,
            "remaining_volume": position.volume,
            "profit": realized,
            "reason": comment,
        })
        if position.volume <= 0.0000001:
            self._close_position(position, exit_price, comment)
        else:
            self._mark_to_market()
        return True


class BacktestTradeLogger:
    """No-op logger used to keep replay isolated from live JSON logs."""

    def __init__(self):
        self.entries = []

    def _save_log(self, entry):
        self.entries.append(dict(entry or {}))

    def log_signal(self, signal):
        self._save_log({"event": "FVG_DETECTED", **(signal or {})})

    def log_trade(self, trade):
        self._save_log({"event": "TRADE_EXECUTED", **(trade or {})})

    def log_close(self, close_info):
        self._save_log({"event": "TRADE_CLOSED", **(close_info or {})})

    def get_logs(self):
        return list(self.entries)

    def get_stats(self):
        return {}


def _write_trade_snapshot_svg(path: str, candles: list[dict], position: ReplayPosition, entry_index: int, exit_price: float, metadata: dict):
    width = 980
    height = 520
    pad_l = 64
    pad_r = 28
    pad_t = 36
    pad_b = 74
    chart_w = width - pad_l - pad_r
    chart_h = height - pad_t - pad_b
    prices = []
    for candle in candles:
        prices.extend([float(candle["high"]), float(candle["low"])])
    prices.extend([position.entry, position.sl, position.tp, exit_price])
    hi = max(prices)
    lo = min(prices)
    span = max(hi - lo, 1e-9)

    def y(price):
        return pad_t + (hi - float(price)) / span * chart_h

    def x(index):
        if len(candles) <= 1:
            return pad_l + chart_w / 2
        return pad_l + index / (len(candles) - 1) * chart_w

    candle_w = max(4, chart_w / max(len(candles), 1) * 0.55)
    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>",
        "<rect width='100%' height='100%' fill='#101418'/>",
        f"<text x='{pad_l}' y='24' fill='#e8edf2' font-family='Arial' font-size='16'>{html.escape(position.symbol)} {html.escape(position.type)} loss replay ticket {position.ticket}</text>",
    ]

    for i, candle in enumerate(candles):
        o = float(candle["open"])
        h = float(candle["high"])
        l = float(candle["low"])
        c = float(candle["close"])
        cx = x(i)
        color = "#47c27c" if c >= o else "#e06666"
        parts.append(f"<line x1='{cx:.2f}' x2='{cx:.2f}' y1='{y(h):.2f}' y2='{y(l):.2f}' stroke='{color}' stroke-width='1.2'/>")
        top = min(y(o), y(c))
        body_h = max(2, abs(y(o) - y(c)))
        stroke = "#f5d76e" if i == entry_index else color
        sw = "2.2" if i == entry_index else "1"
        parts.append(f"<rect x='{cx - candle_w / 2:.2f}' y='{top:.2f}' width='{candle_w:.2f}' height='{body_h:.2f}' fill='{color}' stroke='{stroke}' stroke-width='{sw}'/>")

    def hline(price, label, color):
        yy = y(price)
        parts.append(f"<line x1='{pad_l}' x2='{width-pad_r}' y1='{yy:.2f}' y2='{yy:.2f}' stroke='{color}' stroke-dasharray='5 4' stroke-width='1.4'/>")
        parts.append(f"<text x='{width-pad_r-180}' y='{yy - 6:.2f}' fill='{color}' font-family='Arial' font-size='12'>{html.escape(label)} {float(price):.5f}</text>")

    hline(position.entry, "ENTRY", "#f5d76e")
    hline(position.sl, "SL", "#ff6b6b")
    hline(position.tp, "TP", "#4dabf7")
    hline(exit_price, "EXIT", "#ffffff")

    ict = metadata.get("ict") or {}
    liquidity = ict.get("liquidity") or {}
    structure = ict.get("structure") or {}
    if liquidity.get("buy_side_liquidity"):
        hline(liquidity.get("buy_side_liquidity"), "BUY-SIDE LIQ", "#b197fc")
    if liquidity.get("sell_side_liquidity"):
        hline(liquidity.get("sell_side_liquidity"), "SELL-SIDE LIQ", "#63e6be")
    event = structure.get("bos") or structure.get("choch") or {}
    if event.get("level"):
        hline(event.get("level"), event.get("type", "STRUCTURE"), "#ffa94d")

    diagnostics = metadata.get("entry_diagnostics") or {}
    note = (
        f"session={diagnostics.get('session_name')} | sweep={diagnostics.get('liquidity_sweep')} | "
        f"BOS={diagnostics.get('bos_confirmed')} | CHOCH={diagnostics.get('choch_confirmed')} | "
        f"FVG={diagnostics.get('fvg_present')} | retest={diagnostics.get('fvg_retested')} | "
        f"HTF={diagnostics.get('htf_bias_aligned')} | zone={diagnostics.get('premium_discount_zone')}"
    )
    parts.append(f"<text x='{pad_l}' y='{height-44}' fill='#cbd5df' font-family='Arial' font-size='12'>{html.escape(note)}</text>")
    parts.append(f"<text x='{pad_l}' y='{height-24}' fill='#cbd5df' font-family='Arial' font-size='12'>Structure/liquidity overlays are encoded in the diagnostics line; highlighted candle is the entry candle.</text>")
    parts.append("</svg>")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(parts))


class HistoricalMT5Replay:
    """Monkeypatch MT5 market-data functions for sequential candle replay."""

    def __init__(self, data_by_symbol: dict[str, pd.DataFrame], broker: ReplayBroker):
        self.data_by_symbol = data_by_symbol
        self.broker = broker
        self.cursor = 0
        self._originals = {}

    def _slice(self, symbol: str, start_pos: int, count: int):
        df = self.data_by_symbol.get(symbol)
        if df is None or df.empty:
            return None
        end = max(0, self.cursor - int(start_pos))
        start = max(0, end - int(count) + 1)
        window = df.iloc[start:end + 1]
        if window.empty:
            return None
        return window.iloc[::-1].to_records(index=False)

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        return self._slice(symbol, start_pos, count)

    def copy_rates_range(self, symbol, timeframe, start, end):
        df = self.data_by_symbol.get(symbol)
        if df is None or df.empty:
            return None
        current = df.iloc[:self.cursor + 1]
        if current.empty:
            return None
        return current.to_records(index=False)

    def symbol_info(self, symbol):
        return self.broker.get_symbol_info(symbol)

    def symbol_info_tick(self, symbol):
        return self.broker.get_symbol_tick(symbol)

    @contextmanager
    def patched(self):
        names = ["copy_rates_from_pos", "copy_rates_range", "symbol_info", "symbol_info_tick"]
        self._originals = {name: getattr(mt5, name, None) for name in names}
        mt5.copy_rates_from_pos = self.copy_rates_from_pos
        mt5.copy_rates_range = self.copy_rates_range
        mt5.symbol_info = self.symbol_info
        mt5.symbol_info_tick = self.symbol_info_tick
        try:
            yield
        finally:
            for name, original in self._originals.items():
                if original is not None:
                    setattr(mt5, name, original)


def _load_history(symbols, timeframe, bars: int) -> dict[str, pd.DataFrame]:
    data = {}
    try:
        mt5.initialize()
    except Exception:
        pass
    for symbol in symbols:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
        if rates is None or len(rates) < 100:
            raise RuntimeError(f"Not enough historical candles for {symbol}; requested {bars}, received {0 if rates is None else len(rates)}")
        df = pd.DataFrame(rates).sort_values("time").reset_index(drop=True)
        data[symbol] = df
    return data


def _save_csv(path: str, rows: list[dict]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("")
        return
    keys = sorted({key for row in rows for key in row.keys()})
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def run_replay_backtest(
    symbols=None,
    timeframe=None,
    bars: int = 1500,
    initial_balance: float = 10000.0,
    warmup: int = 220,
    output_dir: str = "data",
) -> dict:
    """Replay historical candles through the live TradingEngine."""
    try:
        from technical_analysis import reset_signal_fingerprints
        reset_signal_fingerprints()
    except Exception:
        pass
    symbols = symbols or [s.strip() for s in os.getenv("TRADING_SYMBOLS", "EURUSD").split(",") if s.strip()]
    timeframe = timeframe or getattr(mt5, "TIMEFRAME_M5", 5)
    data_by_symbol = _load_history(symbols, timeframe, bars)
    min_len = min(len(df) for df in data_by_symbol.values())

    broker = ReplayBroker(initial_balance=initial_balance)
    replay = HistoricalMT5Replay(data_by_symbol, broker)

    engine = TradingEngine()
    engine.mt5 = broker
    engine.logger = BacktestTradeLogger()
    engine.backtest_mode = True
    engine.symbols = symbols
    engine.timeframe = timeframe
    engine.is_running = True
    engine.scan_on_new_candle = False
    engine.scan_interval_seconds = 0
    engine.trade_cooldown_minutes = 0
    engine.duplicate_signal_cooldown_seconds = 0
    engine.trailing_tp_cooldown_seconds = 0
    engine.news_ladder_cooldown_seconds = 0
    engine._is_market_open = lambda: True

    equity_curve = []
    with replay.patched():
        for cursor in range(max(10, warmup), min_len):
            replay.cursor = cursor
            for symbol, df in data_by_symbol.items():
                broker.update_bar(symbol, df.iloc[cursor].to_dict())

            engine.last_scan_at = None
            engine.scan_and_trade()
            engine.check_positions()

            account = broker.get_account_info()
            equity_curve.append({
                "time": datetime.fromtimestamp(int(next(iter(data_by_symbol.values())).iloc[cursor]["time"])).isoformat(),
                "balance": account["balance"],
                "equity": account["equity"],
                "open_positions": len(broker.positions),
                "pending_orders": len(broker.pending_orders),
            })

        for position in list(broker.positions.values()):
            bar = broker.current_bar_by_symbol.get(position.symbol, {})
            broker._close_position(position, float(bar.get("close", position.entry)), "END_OF_BACKTEST")

    trades_path = os.path.join(output_dir, "trades.csv")
    equity_path = os.path.join(output_dir, "equity_curve.csv")
    _save_csv(trades_path, broker.closed_trades)
    _save_csv(equity_path, equity_curve)

    metrics = summarize_performance(broker.closed_trades, equity_curve)
    return {
        "symbols": symbols,
        "bars": min_len,
        "trades_path": trades_path,
        "equity_curve_path": equity_path,
        "trades": broker.closed_trades,
        "equity_curve": equity_curve,
        "metrics": metrics,
    }


def run_strategy_comparison(
    symbols=None,
    timeframe=None,
    bars: int = 1500,
    initial_balance: float = 10000.0,
    output_dir: str = "data",
    diagnostics: bool = False,
) -> dict:
    if diagnostics:
        reset_reports()
    scenarios = [
        ("current", {"ICT_ENABLED": "false"}, "current_strategy"),
        ("ict_strict", {
            "ICT_ENABLED": "true",
            "ICT_REQUIRE_LIQUIDITY_SWEEP": "true",
            "ICT_REQUIRE_FVG_RETEST": "true",
            "ICT_REQUIRE_BOS_OR_CHOCH": "true",
            "MIN_SETUP_SCORE": "0.80",
            "MIN_CONVICTION": "0.70",
            "MIN_RR": "1.5",
            "WAIT_FOR_RETEST": "false",
            "FEATURE_EARLY_ENTRY": "false",
        }, "ict_strict"),
        ("ict_strict_retest", {
            "ICT_ENABLED": "true",
            "ICT_REQUIRE_LIQUIDITY_SWEEP": "true",
            "ICT_REQUIRE_FVG_RETEST": "true",
            "ICT_REQUIRE_BOS_OR_CHOCH": "true",
            "ICT_MIN_RISK_REWARD": "1.5",
            "MIN_SETUP_SCORE": "0.80",
            "MIN_CONVICTION": "0.70",
            "MIN_RR": "1.5",
            "WAIT_FOR_RETEST": "true",
            "FEATURE_EARLY_ENTRY": "false",
            "FEATURE_STRICT_QUALITY_GATE": "true",
        }, "ict_strict_retest"),
    ]
    results = {}
    rows = []
    for key, env, folder in scenarios:
        scenario_dir = os.path.join(output_dir, folder)
        with _temporary_env(env):
            result = run_replay_backtest(
                symbols=symbols,
                timeframe=timeframe,
                bars=bars,
                initial_balance=initial_balance,
                output_dir=scenario_dir,
            )
        metrics = result["metrics"]
        results[key] = result
        rows.append({
            "scenario": key,
            "total_trades": metrics.get("total_trades"),
            "net_profit": metrics.get("net_profit"),
            "win_rate": metrics.get("win_rate"),
            "profit_factor": metrics.get("profit_factor"),
            "expectancy": metrics.get("expectancy"),
            "average_r_multiple": metrics.get("average_r_multiple"),
            "max_drawdown": metrics.get("max_drawdown"),
            "trades_path": result["trades_path"],
        })
    comparison_path = os.path.join(output_dir, "strategy_comparison.csv")
    _save_csv(comparison_path, rows)
    blocker_summary = build_summary() if diagnostics else None
    recommendation = recommend_best_strategy(rows)
    if blocker_summary and blocker_summary.get("recommendation"):
        print(f"ICT diagnostics: {blocker_summary['recommendation'].get('message')}")
        print(f"Recommended next step: {blocker_summary['recommendation'].get('recommended_next_step')}")
    return {
        "comparison_path": comparison_path,
        "rows": rows,
        "results": results,
        "recommendation": recommendation,
        "ict_blocker_summary": blocker_summary,
    }


def recommend_best_strategy(rows: list[dict]) -> dict:
    def safe_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    scored = []
    if not any(safe_float(row.get("total_trades")) > 0 for row in rows):
        return {
            "best_scenario": None,
            "selection_score": None,
            "reason": "No scenario produced closed trades, so no statistical recommendation is valid.",
        }
    for row in rows:
        trades = safe_float(row.get("total_trades"))
        if trades <= 0:
            score = -9999.0
        else:
            score = (
                safe_float(row.get("profit_factor")) * 2.0
                + safe_float(row.get("expectancy")) * 0.01
                + safe_float(row.get("average_r_multiple"))
                + safe_float(row.get("win_rate"))
                - abs(safe_float(row.get("max_drawdown"))) * 0.001
            )
        scored.append({**row, "selection_score": round(score, 4)})
    best = max(scored, key=lambda item: item["selection_score"]) if scored else {}
    return {
        "best_scenario": best.get("scenario"),
        "selection_score": best.get("selection_score"),
        "reason": "Selected by profit factor, expectancy, average R, win rate, drawdown penalty, and nonzero trade count.",
    }


def main():
    parser = argparse.ArgumentParser(description="Replay historical MT5 candles through Nexus TradingEngine.")
    parser.add_argument("--symbols", default=os.getenv("TRADING_SYMBOLS", "EURUSD"), help="Comma-separated symbols")
    parser.add_argument("--timeframe", default="M5", choices=sorted(TIMEFRAME_MAP), help="MT5 timeframe")
    parser.add_argument("--bars", type=int, default=1500, help="Number of historical candles to request from MT5")
    parser.add_argument("--initial-balance", type=float, default=10000.0)
    parser.add_argument("--compare", action="store_true", help="Compare current, ICT, and strict ICT configurations")
    parser.add_argument("--diagnostics", action="store_true", help="Generate ICT blocker and detector diagnostics reports")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    if args.compare:
        comparison = run_strategy_comparison(
            symbols=[s.strip().upper() for s in args.symbols.split(",") if s.strip()],
            timeframe=TIMEFRAME_MAP[args.timeframe],
            bars=args.bars,
            initial_balance=args.initial_balance,
            diagnostics=args.diagnostics,
        )
        for row in comparison["rows"]:
            print(row)
        print(f"Comparison: {comparison['comparison_path']}")
        print(f"Recommended: {comparison['recommendation']}")
        if args.diagnostics:
            print("ICT blocker report: data\\ict_blocker_report.csv")
            print("ICT blocker summary: data\\ict_blocker_summary.json")
            print("ICT near misses: data\\ict_near_miss_setups.csv")
            print("FVG retest audit: data\\fvg_retest_audit.csv")
            print("Liquidity sweep audit: data\\liquidity_sweep_audit.csv")
            print("Structure audit: data\\structure_audit.csv")
        return

    result = run_replay_backtest(
        symbols=[s.strip().upper() for s in args.symbols.split(",") if s.strip()],
        timeframe=TIMEFRAME_MAP[args.timeframe],
        bars=args.bars,
        initial_balance=args.initial_balance,
    )
    print(result["metrics"])
    print(f"Trades: {result['trades_path']}")
    print(f"Equity curve: {result['equity_curve_path']}")


if __name__ == "__main__":
    main()
