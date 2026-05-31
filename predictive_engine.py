"""
Predictive Engine - Machine Learning Price Prediction

This module uses XGBoost to predict future price movements based on
historical patterns and technical indicators.
"""
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import os
import pickle

try:
    import MetaTrader5 as mt5
    from xgboost import XGBClassifier
except ImportError:
    mt5 = None
    XGBClassifier = None

logger = logging.getLogger(__name__)


class PredictiveEngine:
    """Uses ML to predict future price movements."""

    def __init__(self, model_path: str = "models/xgb_model.pkl"):
        self.model_path = model_path
        self.model = None
        self.feature_columns = [
            'time_of_day', 'hour', 'weekday',
            'returns_1', 'returns_5', 'returns_15',
            'volatility_5', 'volatility_15', 'volatility_60',
            'volume_ratio', 'volume_ma_ratio',
            'rsi_14', 'macd', 'macd_signal', 'macd_hist',
            'bb_upper_ratio', 'bb_lower_ratio', 'bb_position',
            'ema_50_ratio', 'ema_200_ratio',
            'atr_ratio', 'adx',
            'wick_ratio', 'body_ratio',
            'support_resistance_dist'
        ]

        self._load_or_train_model()

    def predict_probability(self, symbol: str, timeframe: int = mt5.TIMEFRAME_M5) -> Dict:
        """
        Predict probability of bullish movement in next 3 candles.

        Args:
            symbol: Trading symbol
            timeframe: MT5 timeframe constant

        Returns:
            Dict with prediction results
        """
        try:
            if not self.model:
                return {"probability": 0.5, "confidence": 0.0, "prediction": "NEUTRAL", "error": "No model available"}

            # Extract features from recent data
            features = self._extract_features(symbol, timeframe)
            if not features:
                return {"probability": 0.5, "confidence": 0.0, "prediction": "NEUTRAL", "error": "Could not extract features"}

            # Make prediction
            features_df = pd.DataFrame([features])
            probability = self.model.predict_proba(features_df)[0][1]  # Probability of class 1 (bullish)

            # Calculate confidence based on prediction certainty
            confidence = abs(probability - 0.5) * 2  # 0-1 scale

            result = {
                "probability": float(probability),
                "confidence": float(confidence),
                "prediction": "BULLISH" if probability > 0.5 else "BEARISH",
                "strength": self._classify_strength(probability)
            }

            logger.info(f"Predictive analysis for {symbol}: {probability:.3f} probability bullish ({result['strength']})")
            return result

        except Exception as e:
            logger.error(f"Error in prediction: {e}")
            return {"probability": 0.5, "confidence": 0.0, "error": str(e)}

    def _extract_features(self, symbol: str, timeframe: int) -> Optional[Dict]:
        """Extract ML features from market data."""
        try:
            if not mt5 or not mt5.initialize():
                return None

            # Get sufficient historical data
            bars = mt5.copy_rates_from_pos(symbol, timeframe, 0, 200)
            if bars is None or len(bars) < 150:
                return None

            df = pd.DataFrame(bars)
            df['time'] = pd.to_datetime(df['time'], unit='s')

            # Basic time features
            current_time = datetime.now()
            features = {
                'time_of_day': current_time.hour + current_time.minute / 60.0,
                'hour': current_time.hour,
                'weekday': current_time.weekday()
            }

            # Price returns
            df['returns'] = df['close'].pct_change()
            features.update({
                'returns_1': df['returns'].iloc[-1],
                'returns_5': df['returns'].iloc[-5:].mean(),
                'returns_15': df['returns'].iloc[-15:].mean()
            })

            # Volatility
            features.update({
                'volatility_5': df['returns'].iloc[-5:].std(),
                'volatility_15': df['returns'].iloc[-15:].std(),
                'volatility_60': df['returns'].iloc[-60:].std()
            })

            # Volume features
            df['volume_ma'] = df['tick_volume'].rolling(20).mean()
            features.update({
                'volume_ratio': df['tick_volume'].iloc[-1] / df['tick_volume'].iloc[-2] if df['tick_volume'].iloc[-2] > 0 else 1.0,
                'volume_ma_ratio': df['tick_volume'].iloc[-1] / df['volume_ma'].iloc[-1] if df['volume_ma'].iloc[-1] > 0 else 1.0
            })

            # Technical indicators
            features.update(self._calculate_technical_indicators(df))

            # Candle patterns
            features.update(self._calculate_candle_features(df))

            return features

        except Exception as e:
            logger.error(f"Error extracting features: {e}")
            return None

    def _calculate_technical_indicators(self, df: pd.DataFrame) -> Dict:
        """Calculate technical indicators for features."""
        features = {}

        try:
            close = df['close']
            high = df['high']
            low = df['low']

            # RSI
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            features['rsi_14'] = 100 - (100 / (1 + rs)).iloc[-1]

            # MACD
            ema12 = close.ewm(span=12).mean()
            ema26 = close.ewm(span=26).mean()
            macd = ema12 - ema26
            signal = macd.ewm(span=9).mean()
            features.update({
                'macd': macd.iloc[-1],
                'macd_signal': signal.iloc[-1],
                'macd_hist': (macd - signal).iloc[-1]
            })

            # Bollinger Bands
            sma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            bb_upper = sma20 + (std20 * 2)
            bb_lower = sma20 - (std20 * 2)
            features.update({
                'bb_upper_ratio': bb_upper.iloc[-1] / close.iloc[-1],
                'bb_lower_ratio': bb_lower.iloc[-1] / close.iloc[-1],
                'bb_position': (close.iloc[-1] - bb_lower.iloc[-1]) / (bb_upper.iloc[-1] - bb_lower.iloc[-1])
            })

            # EMAs
            ema50 = close.ewm(span=50).mean()
            ema200 = close.ewm(span=200).mean()
            features.update({
                'ema_50_ratio': close.iloc[-1] / ema50.iloc[-1],
                'ema_200_ratio': close.iloc[-1] / ema200.iloc[-1]
            })

            # ATR
            tr = pd.concat([
                high - low,
                abs(high - close.shift(1)),
                abs(low - close.shift(1))
            ], axis=1).max(axis=1)
            atr = tr.rolling(14).mean()
            features['atr_ratio'] = atr.iloc[-1] / close.iloc[-1]

            # ADX (simplified)
            features['adx'] = 25.0  # Placeholder - full ADX calculation is complex

        except Exception as e:
            logger.warning(f"Error calculating technical indicators: {e}")
            # Fill with neutral values
            features.update({
                'rsi_14': 50.0, 'macd': 0.0, 'macd_signal': 0.0, 'macd_hist': 0.0,
                'bb_upper_ratio': 1.02, 'bb_lower_ratio': 0.98, 'bb_position': 0.5,
                'ema_50_ratio': 1.0, 'ema_200_ratio': 1.0, 'atr_ratio': 0.01, 'adx': 25.0
            })

        return features

    def _calculate_candle_features(self, df: pd.DataFrame) -> Dict:
        """Calculate candle pattern features."""
        try:
            last_candle = df.iloc[-1]
            open_price = last_candle['open']
            close_price = last_candle['close']
            high_price = last_candle['high']
            low_price = last_candle['low']

            body_size = abs(close_price - open_price)
            total_range = high_price - low_price

            features = {
                'wick_ratio': (total_range - body_size) / total_range if total_range > 0 else 0.5,
                'body_ratio': body_size / total_range if total_range > 0 else 0.5,
                'support_resistance_dist': 0.5  # Placeholder for S/R distance
            }

            return features

        except Exception as e:
            logger.warning(f"Error calculating candle features: {e}")
            return {'wick_ratio': 0.5, 'body_ratio': 0.5, 'support_resistance_dist': 0.5}

    def _load_or_train_model(self):
        """Load existing model or train a new one."""
        try:
            if os.path.exists(self.model_path):
                with open(self.model_path, 'rb') as f:
                    self.model = pickle.load(f)
                logger.info(f"Loaded ML model from {self.model_path}")
            else:
                logger.warning(f"No ML model found at {self.model_path}. Using dummy predictions.")
                self.model = None
                # In a real implementation, you would train the model here
                # self._train_model()

        except Exception as e:
            logger.error(f"Error loading model: {e}")
            self.model = None

    def _train_model(self):
        """Train the XGBoost model (placeholder for actual training)."""
        # This would require historical data with labels
        # For now, we'll use a dummy model
        logger.info("Training XGBoost model...")

        # Create dummy training data
        np.random.seed(42)
        n_samples = 10000
        X = np.random.randn(n_samples, len(self.feature_columns))
        y = np.random.randint(0, 2, n_samples)  # 0=bearish, 1=bullish

        if XGBClassifier:
            self.model = XGBClassifier(
                n_estimators=100,
                max_depth=6,
                learning_rate=0.1,
                random_state=42
            )
            self.model.fit(X, y)

            # Save model
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            with open(self.model_path, 'wb') as f:
                pickle.dump(self.model, f)

            logger.info(f"Trained and saved ML model to {self.model_path}")
        else:
            logger.warning("XGBoost not available, using dummy model")

    def _classify_strength(self, probability: float) -> str:
        """Classify prediction strength."""
        if probability >= 0.7:
            return "VERY_STRONG_BULLISH"
        elif probability >= 0.6:
            return "STRONG_BULLISH"
        elif probability >= 0.55:
            return "MODERATE_BULLISH"
        elif probability <= 0.3:
            return "VERY_STRONG_BEARISH"
        elif probability <= 0.4:
            return "STRONG_BEARISH"
        elif probability <= 0.45:
            return "MODERATE_BEARISH"
        else:
            return "NEUTRAL"