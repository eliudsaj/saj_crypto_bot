from engine import TradingEngine


class FakeMT5:
    last_order_error = None

    def get_symbol_info(self, symbol):
        return type("Info", (), {
            "digits": 5,
            "volume_min": 0.01,
            "volume_max": 100,
            "volume_step": 0.01,
            "trade_tick_value": 1.0,
            "trade_tick_size": 0.0001,
        })()

    def get_symbol_tick(self, symbol):
        return type("Tick", (), {"ask": 1.1000, "bid": 1.0999})()

    def place_buy_order(self, symbol, volume, price, sl, tp):
        return 123456

    def place_sell_order(self, symbol, volume, price, sl, tp):
        return 123457

    def place_buy_limit_order(self, symbol, volume, price, sl, tp):
        return 123458

    def place_sell_limit_order(self, symbol, volume, price, sl, tp):
        return 123459


engine = TradingEngine()
engine.mt5 = FakeMT5()
signal = {
    'symbol': 'EURUSD',
    'action': 'BUY',
    'entry': 1.1000,
    'sl': 1.0980,
    'tp': 1.1040,
    'type': 'FVG',
    'nature': 'FVG BUY',
    'gap_size': 20,
}
signal['trade_style'] = engine._classify_trade_style(signal, {'label': 'Scalp Potential'})
engine.execute_trade(signal, volume=0.1, use_market_execution=True)
print('active_trades:', engine.active_trades)
print('trade_style:', engine.active_trades['EURUSD']['trade_style'] if 'EURUSD' in engine.active_trades else 'MISSING')
