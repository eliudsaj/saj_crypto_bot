"""
Backtesting Module - Test trading strategies on historical data
"""
import logging
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime, timedelta
from technical_analysis import detect_fvg
from bible_logic import validate_trade
from engine import TradingEngine

logger = logging.getLogger(__name__)


class Backtester:
    def __init__(self, symbol, start_date, end_date, timeframe=mt5.TIMEFRAME_M5):
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.timeframe = timeframe
        self.trades = []
        self.equity_curve = []
        self.initial_balance = 10000  # Starting balance for backtest

    def load_historical_data(self):
        """Load historical data for backtesting"""
        try:
            # Convert dates to MT5 format
            start = datetime.strptime(self.start_date, "%Y-%m-%d")
            end = datetime.strptime(self.end_date, "%Y-%m-%d")

            # Get historical rates
            rates = mt5.copy_rates_range(self.symbol, self.timeframe, start, end)
            if rates is None:
                logger.error(f"Failed to load historical data for {self.symbol}")
                return None

            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            return df
        except Exception as e:
            logger.error(f"Error loading historical data: {e}")
            return None

    def simulate_trading(self, rule_config=None):
        """Simulate trading on historical data"""
        if rule_config is None:
            rule_config = {"ema": True, "volume": True, "po3": False}  # Relaxed for backtesting

        df = self.load_historical_data()
        if df is None or df.empty:
            return None

        balance = self.initial_balance
        position = None
        trades = []

        logger.info(f"Starting backtest for {self.symbol} from {self.start_date} to {self.end_date}")

        for i in range(50, len(df)):  # Start from bar 50 to have enough history
            current_bar = df.iloc[i]
            current_time = current_bar['time']

            # Check for signals using recent bars
            recent_rates = df.iloc[i-5:i+1].to_records()
            signal = self._detect_signal_from_rates(recent_rates)

            if signal:
                # Validate the signal
                valid, reason = validate_trade(self.symbol, {**rule_config, **{"action": signal.get("action")}})
                if valid:
                    # Execute trade
                    if position is None:  # No open position
                        entry_price = signal['entry']
                        sl = signal['sl']
                        tp = signal['tp']

                        # Calculate position size (simplified)
                        risk_amount = balance * 0.02  # 2% risk
                        pip_value = 10  # Simplified pip value
                        stop_pips = abs(entry_price - sl) / 0.0001  # Assume 4-digit broker
                        volume = risk_amount / (stop_pips * pip_value)

                        position = {
                            'entry_time': current_time,
                            'entry_price': entry_price,
                            'sl': sl,
                            'tp': tp,
                            'volume': volume,
                            'action': signal['action'],
                            'risk_amount': risk_amount
                        }
                        logger.info(f"BACKTEST: Opened {signal['action']} at {entry_price:.5f}")
                else:
                    logger.debug(f"Signal rejected: {reason}")

            # Check if position should be closed
            if position:
                current_price = current_bar['close']

                # Check stop loss
                if position['action'] == 'BUY':
                    if current_price <= position['sl']:
                        # Stop loss hit
                        exit_price = position['sl']
                        profit = (exit_price - position['entry_price']) * position['volume'] * 100000  # Simplified P&L
                        balance += profit
                        trades.append({
                            'entry_time': position['entry_time'],
                            'exit_time': current_time,
                            'profit': profit,
                            'action': position['action'],
                            'reason': 'SL'
                        })
                        position = None
                        logger.info(f"BACKTEST: SL hit, P&L: {profit:.2f}")
                    elif current_price >= position['tp']:
                        # Take profit hit
                        exit_price = position['tp']
                        profit = (exit_price - position['entry_price']) * position['volume'] * 100000
                        balance += profit
                        trades.append({
                            'entry_time': position['entry_time'],
                            'exit_time': current_time,
                            'profit': profit,
                            'action': position['action'],
                            'reason': 'TP'
                        })
                        position = None
                        logger.info(f"BACKTEST: TP hit, P&L: {profit:.2f}")
                else:  # SELL
                    if current_price >= position['sl']:
                        exit_price = position['sl']
                        profit = (position['entry_price'] - exit_price) * position['volume'] * 100000
                        balance += profit
                        trades.append({
                            'entry_time': position['entry_time'],
                            'exit_time': current_time,
                            'profit': profit,
                            'action': position['action'],
                            'reason': 'SL'
                        })
                        position = None
                        logger.info(f"BACKTEST: SL hit, P&L: {profit:.2f}")
                    elif current_price <= position['tp']:
                        exit_price = position['tp']
                        profit = (position['entry_price'] - exit_price) * position['volume'] * 100000
                        balance += profit
                        trades.append({
                            'entry_time': position['entry_time'],
                            'exit_time': current_time,
                            'profit': profit,
                            'action': position['action'],
                            'reason': 'TP'
                        })
                        position = None
                        logger.info(f"BACKTEST: TP hit, P&L: {profit:.2f}")

            # Record equity
            self.equity_curve.append({
                'time': current_time,
                'equity': balance
            })

        self.trades = trades
        return {
            'trades': trades,
            'final_balance': balance,
            'total_return': (balance - self.initial_balance) / self.initial_balance * 100,
            'win_rate': len([t for t in trades if t['profit'] > 0]) / len(trades) * 100 if trades else 0
        }

    def _detect_signal_from_rates(self, rates):
        """Detect FVG signal from raw rates data"""
        try:
            # Convert to format expected by detect_fvg
            # This is a simplified version - in practice you'd need to adapt detect_fvg
            if len(rates) < 3:
                return None

            # Mock the detection for backtesting
            # In a real implementation, you'd modify detect_fvg to work with historical data
            return None  # Placeholder

        except Exception as e:
            logger.error(f"Error detecting signal: {e}")
            return None

    def print_results(self, results):
        """Print backtest results"""
        if not results:
            print("No backtest results available")
            return

        print(f"\n=== Backtest Results for {self.symbol} ===")
        print(f"Period: {self.start_date} to {self.end_date}")
        print(f"Initial Balance: ${self.initial_balance:.2f}")
        print(f"Final Balance: ${results['final_balance']:.2f}")
        print(f"Total Return: {results['total_return']:.2f}%")
        print(f"Total Trades: {len(results['trades'])}")
        print(f"Win Rate: {results['win_rate']:.1f}%")

        if results['trades']:
            profits = [t['profit'] for t in results['trades']]
            print(f"Average Win: ${sum(p for p in profits if p > 0) / len([p for p in profits if p > 0]):.2f}")
            print(f"Average Loss: ${sum(p for p in profits if p < 0) / len([p for p in profits if p < 0]):.2f}")
            print(f"Largest Win: ${max(profits):.2f}")
            print(f"Largest Loss: ${min(profits):.2f}")


def run_backtest(symbol="EURUSD", days=30):
    """Run a quick backtest"""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    backtester = Backtester(symbol, start_date, end_date)
    results = backtester.simulate_trading()
    backtester.print_results(results)
    return results