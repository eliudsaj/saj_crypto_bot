#!/usr/bin/env python3
"""
Test the new War Room system
"""
import sys
sys.path.append('.')
import logging

logging.basicConfig(level=logging.INFO)

from analytic_engine import AnalyticEngine
from predictive_engine import PredictiveEngine
from ensemble_decision import EnsembleDecision

def test_war_room():
    """Test the complete War Room system"""
    print("=== Testing War Room System ===\n")

    # Initialize engines
    analytic = AnalyticEngine()
    predictive = PredictiveEngine()
    ensemble = EnsembleDecision()

    # Test symbols
    symbols = ["EURUSD", "GBPUSD"]

    for symbol in symbols:
        print(f"Testing {symbol}...")

        # Mock signal (in real usage this comes from technical_analysis)
        mock_signal = {
            "symbol": symbol,
            "action": "BUY",
            "entry": 1.0500,
            "sl": 1.0450,
            "tp": 1.0600,
            "gap_size": 0.0005,
            "nature": "Bullish Pullback (Trend Continuation)"
        }

        # Get analytic evaluation
        analytic_result = analytic.evaluate_setup(symbol, mock_signal)
        print(f"  Analytic Score: {analytic_result['overall_score']:.3f}")
        print(f"  Quality: {analytic_result['quality_description']}")

        # Get predictive evaluation
        predictive_result = predictive.predict_probability(symbol)
        print(f"  Predictive Probability: {predictive_result['probability']:.3f}")
        print(f"  Prediction: {predictive_result['prediction']}")

        # Get ensemble decision
        ensemble_decision = ensemble.make_decision(analytic_result, predictive_result, mock_signal)
        print(f"  Ensemble Conviction: {ensemble_decision['conviction']:.3f}")
        print(f"  Final Decision: {ensemble_decision['decision']}")
        print(f"  Reasoning: {ensemble_decision['reasoning']}")
        print()

if __name__ == "__main__":
    test_war_room()