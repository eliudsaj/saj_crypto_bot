#!/usr/bin/env python3
"""
Train the XGBoost model for the Predictive Engine
"""
import sys
sys.path.append('.')
import logging

logging.basicConfig(level=logging.INFO)

from predictive_engine import PredictiveEngine

def train_model():
    """Train and save the XGBoost model"""
    print("=== Training XGBoost Model ===\n")

    # Initialize predictive engine
    engine = PredictiveEngine()

    # Force training of the model
    print("Training model with synthetic data...")
    engine._train_model()

    # Test the model
    print("\nTesting trained model...")
    test_result = engine.predict_probability("EURUSD")
    print(f"Test prediction: {test_result}")

    print("\nModel training complete!")

if __name__ == "__main__":
    train_model()