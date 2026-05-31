#!/usr/bin/env python3
"""
Test FVG detection directly
"""
import sys
sys.path.append('.')
import MetaTrader5 as mt5
from technical_analysis import detect_fvg
import logging

logging.basicConfig(level=logging.DEBUG)

def test_fvg_detection():
    """Test FVG detection for a specific symbol"""
    if not mt5.initialize():
        print("MT5 not initialized")
        return

    symbols = ["EURUSD", "GBPUSD", "USDJPY"]
    for symbol in symbols:
        print(f"\nTesting {symbol}...")
        signal = detect_fvg(symbol)
        if signal:
            print(f"Found signal: {signal}")
        else:
            print("No signal found")

if __name__ == "__main__":
    test_fvg_detection()