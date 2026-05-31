from brokers.profiles import BrokerProfileManager
from brokers.binance_adapter import BinanceBrokerAdapter
from brokers.mt5_adapter import MT5BrokerAdapter
from engine import TradingEngine


class DummyMT5Interface:
    def __init__(self):
        self.is_connected = False
        self.last_order_error = None
        self.account = None
        self.password = None
        self.server = None
        self.buy_orders = []

    def connect(self):
        self.is_connected = True
        return True

    def ensure_connected(self):
        return self.is_connected or self.connect()

    def disconnect(self):
        self.is_connected = False

    def get_account_info(self):
        return {"balance": 1000, "equity": 1005, "free_margin": 900, "margin_level": 500, "currency": "USD"}

    def get_positions(self):
        return []

    def get_symbol_info(self, symbol):
        return {"symbol": symbol}

    def place_buy_order(self, symbol, volume, price, sl, tp):
        self.buy_orders.append((symbol, volume, price, sl, tp))
        return 123

    def modify_position_sltp(self, ticket, symbol, sl=None, tp=None):
        return ticket == 123 and symbol == "EURUSD"

    def close_position_volume(self, ticket, volume=None, comment="BROKER_CLOSE"):
        return ticket == 123


def test_broker_profile_crud_and_active_switch(tmp_path):
    manager = BrokerProfileManager(db_path=str(tmp_path / "brokers.db"))
    profiles = manager.list_profiles()
    assert len(profiles) == 1
    assert profiles[0]["broker_type"] == "mt5"

    added = manager.add_profile({
        "name": "Demo MT5",
        "broker_type": "mt5",
        "account": "123",
        "server": "Demo",
        "password": "secret",
        "is_active": True,
    })
    assert added["is_active"] is True
    assert added["password_configured"] is True

    updated = manager.update_profile(added["id"], {"name": "Demo MT5 Edited"})
    assert updated["name"] == "Demo MT5 Edited"

    disabled = manager.disable_profile(added["id"], True)
    assert disabled["is_disabled"] is True
    assert disabled["is_active"] is False


def test_mt5_adapter_implements_required_execution_methods():
    dummy = DummyMT5Interface()
    adapter = MT5BrokerAdapter(
        {"name": "Demo", "account": "123", "password": "secret", "server": "Server"},
        interface=dummy,
    )

    assert adapter.connect() is True
    assert adapter.get_account_info()["equity"] == 1005
    assert adapter.get_positions() == []
    assert adapter.get_symbol_info("EURUSD") == {"symbol": "EURUSD"}
    assert adapter.place_order(symbol="EURUSD", action="BUY", volume=0.01, price=1.1, sl=1.0, tp=1.2) == 123
    assert adapter.modify_order(123, symbol="EURUSD", sl=1.05, tp=1.2) is True
    assert adapter.close_order(123) is True
    adapter.disconnect()
    assert adapter.is_connected is False


def test_binance_adapter_defaults_to_paper_orders_without_live_api_call(monkeypatch):
    calls = []

    def fail_request(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("paper order should not call Binance API")

    adapter = BinanceBrokerAdapter({
        "name": "Binance Paper",
        "broker_type": "binance",
        "account": "",
        "password": "",
        "metadata": {"paper": True, "testnet": True, "market": "spot"},
    })
    monkeypatch.setattr(adapter, "_request", fail_request)

    assert adapter.connect() is True
    order_id = adapter.place_order(symbol="BTCUSDT", action="BUY", volume=0.001, order_type="market")
    assert order_id
    assert calls == []


def test_binance_paper_connect_ignores_present_api_key(monkeypatch):
    adapter = BinanceBrokerAdapter({
        "name": "Binance Paper With Key",
        "broker_type": "binance",
        "account": "key",
        "password": "secret",
        "metadata": {"paper": True, "testnet": True, "market": "spot"},
    })
    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("paper connect should not call API")))

    assert adapter.connect() is True
    assert adapter.get_account_info()["account_type"] == "SPOT_PAPER"
    assert adapter.get_account_info()["equity"] == 10000


def test_binance_adapter_maps_signed_account_response(monkeypatch):
    adapter = BinanceBrokerAdapter({
        "name": "Binance Testnet",
        "broker_type": "binance",
        "account": "key",
        "password": "secret",
        "metadata": {"paper": False, "testnet": True, "market": "spot"},
    })

    def fake_request(method, path, params=None, signed=False):
        assert method == "GET"
        assert path == "/api/v3/account"
        assert signed is True
        return {
            "accountType": "SPOT",
            "canTrade": True,
            "balances": [
                {"asset": "USDT", "free": "125.50", "locked": "4.50"},
                {"asset": "BTC", "free": "0.01", "locked": "0"},
            ],
        }

    monkeypatch.setattr(adapter, "_request", fake_request)
    info = adapter.get_account_info()
    assert info["balance"] == 130.0
    assert info["equity"] == 130.0
    assert info["currency"] == "USDT"


def test_binance_live_requires_ack_before_real_order(monkeypatch):
    calls = []
    adapter = BinanceBrokerAdapter({
        "name": "Binance Live Requested",
        "broker_type": "binance",
        "account": "key",
        "password": "secret",
        "metadata": {
            "paper": False,
            "testnet": False,
            "market": "spot",
            "live_trading_enabled": True,
            "live_ack": "",
        },
    })
    monkeypatch.setattr(adapter, "_request", lambda *args, **kwargs: calls.append(args) or {"orderId": 999})

    order_id = adapter.place_order(symbol="BTCUSDT", action="BUY", volume=0.001)
    assert order_id != 999
    assert calls == []


def test_binance_live_ack_allows_real_order_request(monkeypatch):
    calls = []
    adapter = BinanceBrokerAdapter({
        "name": "Binance Live",
        "broker_type": "binance",
        "account": "key",
        "password": "secret",
        "metadata": {
            "paper": False,
            "testnet": False,
            "market": "spot",
            "live_trading_enabled": True,
            "live_ack": "I_UNDERSTAND_BINANCE_LIVE_RISK",
        },
    })

    def fake_request(method, path, params=None, signed=False):
        calls.append((method, path, params, signed))
        return {"orderId": 999}

    monkeypatch.setattr(adapter, "_request", fake_request)
    assert adapter.place_order(symbol="BTCUSDT", action="BUY", volume=0.001) == 999
    assert calls == [("POST", "/api/v3/order", {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "MARKET",
        "quantity": "0.001",
        "newOrderRespType": "RESULT",
    }, True)]


def test_binance_rejects_invalid_server_override():
    try:
        BinanceBrokerAdapter({
            "name": "Bad Binance",
            "broker_type": "binance",
            "server": "admin@nexus.local",
            "metadata": {"paper": True, "testnet": True, "market": "spot"},
        })
    except ValueError as exc:
        assert "full API URL or blank" in str(exc)
    else:
        raise AssertionError("invalid Binance server override should be rejected")


def test_engine_blocks_forex_symbols_when_binance_active(tmp_path, monkeypatch):
    db_path = str(tmp_path / "brokers.db")
    manager = BrokerProfileManager(db_path=db_path)
    binance = manager.add_profile({
        "name": "Binance",
        "broker_type": "binance",
        "metadata": {"paper": True, "testnet": True},
        "is_active": True,
    })
    monkeypatch.setenv("BROKER_DB_PATH", db_path)
    monkeypatch.setenv("BINANCE_TRADING_SYMBOLS", "EURUSD,BTCUSDT")

    from brokers import profiles as profile_module
    profile_module._manager = None
    engine = TradingEngine()
    assert engine.broker_profile["id"] == binance["id"]
    assert "Binance cannot trade forex/CFD symbols" in engine.startup_validation_error
