from engine import TradingEngine
from pending_order_manager import PendingOrderManager
from mt5_interface import MT5Interface, mt5


class FakeInfo:
    digits = 5
    volume_min = 0.01
    volume_max = 100
    volume_step = 0.01
    trade_tick_value = 1.0
    trade_tick_size = 0.0001


class FakeMT5:
    is_connected = True
    last_order_error = None

    def __init__(self):
        self.calls = []

    def get_symbol_info(self, symbol):
        return FakeInfo()

    def get_account_info(self):
        return {"equity": 100000.0, "currency": "USD"}

    def get_symbol_tick(self, symbol):
        return type("Tick", (), {"bid": 1.1, "ask": 1.1001})()

    def place_buy_order(self, symbol, volume, price, sl, tp):
        self.calls.append(("buy", symbol, volume, price, sl, tp))
        return 111

    def place_buy_limit_order(self, symbol, volume, price, sl, tp):
        self.calls.append(("buy_limit", symbol, volume, price, sl, tp))
        return 222


def make_engine():
    engine = TradingEngine.__new__(TradingEngine)
    engine.mt5 = FakeMT5()
    engine.active_trades = {}
    engine.logger = type("Logger", (), {"log_trade": lambda self, trade: None})()
    engine.volume = 0.01
    engine.risk_pct = 0.01
    engine.position_sizing_mode = "fixed"
    engine.take_profit_r_multiplier = 2.0
    engine.take_profit_r_multiplier_scalp = 1.5
    engine.min_expected_r = 1.2
    engine.min_expected_r_scalp = 0.8
    return engine


def test_fixed_lot_sizing_uses_trade_volume():
    engine = make_engine()
    assert engine._calculate_volume("EURUSD", 1.1000, 1.0990) == 0.01


def test_fixed_lot_below_broker_min_rejects_instead_of_rounding_up():
    engine = make_engine()
    engine.volume = 0.001
    assert engine._calculate_volume("EURUSD", 1.1000, 1.0990) == 0.0


def test_tp_is_normalized_from_sl_distance_and_target_r():
    engine = make_engine()
    signal = {
        "symbol": "EURUSD",
        "action": "BUY",
        "entry": 1.1000,
        "sl": 1.0990,
        "tp": 1.1010,
        "trade_style": "Long Intraday",
    }
    ok, reason = engine._normalize_signal_levels_to_rr(signal)
    assert ok, reason
    assert round(signal["tp"], 5) == 1.1020
    assert round(abs(signal["tp"] - signal["entry"]) / abs(signal["entry"] - signal["sl"]), 2) == 2.0


def test_pending_order_manager_uses_rr_ratio_for_tp():
    manager = PendingOrderManager.__new__(PendingOrderManager)
    entry = 1.1000
    sl = 1.0990
    tp = entry + (abs(entry - sl) * 2.0)
    assert round(tp, 5) == 1.1020


def test_pending_order_placed_retcode_is_success(monkeypatch):
    interface = MT5Interface.__new__(MT5Interface)
    interface.is_connected = True
    interface.last_order_error = None

    monkeypatch.setattr(mt5, "symbol_info", lambda symbol: type("Info", (), {"filling_mode": mt5.ORDER_FILLING_IOC})())
    monkeypatch.setattr(
        mt5,
        "order_send",
        lambda request: type("Result", (), {"retcode": mt5.TRADE_RETCODE_PLACED, "order": 987654, "comment": "placed"})(),
    )

    assert interface.place_buy_limit_order("EURUSD", 0.01, 1.1, 1.099, 1.102) == 987654


def test_market_order_price_changed_retcode_does_not_raise(monkeypatch):
    interface = MT5Interface.__new__(MT5Interface)
    interface.is_connected = True
    interface.last_order_error = None

    monkeypatch.setattr(mt5, "symbol_info", lambda symbol: type("Info", (), {"filling_mode": mt5.ORDER_FILLING_IOC})())
    monkeypatch.setattr(
        mt5,
        "order_send",
        lambda request: type(
            "Result",
            (),
            {
                "retcode": mt5.TRADE_RETCODE_PRICE_CHANGED,
                "order": 0,
                "comment": "price changed",
            },
        )(),
    )

    assert interface.place_sell_order("EURUSD", 0.01, 1.1, 1.101, 1.098) is None
    assert "Price changed before execution" in interface.last_order_error


def test_execute_trade_market_uses_fixed_volume_and_mt5_order():
    engine = make_engine()
    engine.add_logic = lambda *args, **kwargs: None
    engine._register_trade_open = lambda symbol: None
    signal = {
        "symbol": "EURUSD",
        "action": "BUY",
        "entry": 1.1000,
        "sl": 1.0990,
        "tp": 1.1020,
        "trade_style": "Long Intraday",
    }
    engine.execute_trade(signal, 0.01, use_market_execution=True)
    assert engine.mt5.calls[0][0] == "buy"
    assert engine.mt5.calls[0][2] == 0.01
    assert "EURUSD" in engine.active_trades


def test_execute_trade_pending_uses_fixed_volume_and_limit_order():
    engine = make_engine()
    engine.add_logic = lambda *args, **kwargs: None
    engine._register_trade_open = lambda symbol: None
    signal = {
        "symbol": "EURUSD",
        "action": "BUY",
        "entry": 1.1000,
        "sl": 1.0990,
        "tp": 1.1020,
        "trade_style": "Long Intraday",
    }
    engine.execute_trade(signal, 0.01, use_market_execution=False)
    assert engine.mt5.calls[0][0] == "buy_limit"
    assert engine.mt5.calls[0][2] == 0.01
    assert "EURUSD" in engine.active_trades
