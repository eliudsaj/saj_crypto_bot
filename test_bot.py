#!/usr/bin/env python3
"""
Test script to validate the trading bot fixes
"""
import sys
sys.path.append('.')

from bible_logic import validate_trade
from technical_analysis import scan_symbols
from mt5_interface import MT5Interface

def test_validation():
    """Test the validation rules"""
    print("Testing validation rules...")

    # Test with relaxed rules - disable rules that require MT5
    config = {
        "ema": False,  # Disable EMA check for testing
        "volume": False,  # Disable volume check for testing
        "po3": False,  # Disable PO3 check for testing
        "action": "BUY"
    }

    # Test EURUSD
    valid, reason = validate_trade("EURUSD", config)
    print(f"EURUSD BUY validation: {valid} - {reason}")

    config["action"] = "SELL"
    valid, reason = validate_trade("EURUSD", config)
    print(f"EURUSD SELL validation: {valid} - {reason}")

    # Test with some rules enabled
    config = {
        "ema": True,
        "volume": True,
        "po3": True,
        "action": "BUY"
    }
    valid, reason = validate_trade("EURUSD", config)
    print(f"EURUSD BUY with all rules: {valid} - {reason}")

def test_signal_detection():
    """Test signal detection"""
    print("\nTesting signal detection...")
    signals = scan_symbols(["EURUSD", "GBPUSD"])
    print(f"Found {len(signals)} signals")
    for signal in signals[:3]:  # Show first 3
        print(f"  {signal}")

if __name__ == "__main__":
    test_validation()
    test_signal_detection()
