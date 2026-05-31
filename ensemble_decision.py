"""
Ensemble Decision - War Room Decision Making

Combines Analytic and Predictive engine outputs using fuzzy logic
to make final trading decisions.
"""
import logging
import os
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


class EnsembleDecision:
    """Combines analytic and predictive scores for final decision."""

    def __init__(self, analytic_weight: float = 0.6, predictive_weight: float = 0.4):
        """
        Initialize ensemble decision maker.

        Args:
            analytic_weight: Weight for analytic score (0.0-1.0)
            predictive_weight: Weight for predictive score (0.0-1.0)
        """
        self.analytic_weight = analytic_weight
        self.predictive_weight = predictive_weight
        self.decision_threshold = float(os.getenv("ENSEMBLE_DECISION_THRESHOLD", 0.60))

        # Ensure weights sum to 1.0
        total_weight = analytic_weight + predictive_weight
        if total_weight != 1.0:
            self.analytic_weight /= total_weight
            self.predictive_weight /= total_weight

    def make_decision(self, analytic_result: Dict, predictive_result: Dict,
                     signal: Dict) -> Dict:
        """
        Make final trading decision combining both engines.

        Args:
            analytic_result: Output from AnalyticEngine.evaluate_setup()
            predictive_result: Output from PredictiveEngine.predict_probability()
            signal: Original trading signal

        Returns:
            Dict with final decision and conviction level
        """
        try:
            # Extract scores
            analytic_score = analytic_result.get("overall_score", 0.5)
            predictive_prob = predictive_result.get("probability", 0.5)

            # For bearish signals, invert predictive probability
            # (if signal is SELL but model predicts bullish, that's bad)
            signal_action = signal.get("action", "").upper()
            if signal_action == "SELL":
                predictive_prob = 1.0 - predictive_prob

            # Calculate ensemble conviction
            conviction = (analytic_score * self.analytic_weight +
                         predictive_prob * self.predictive_weight)

            # Make decision based on conviction threshold
            decision = {
                "conviction": conviction,
                "decision": "TRADE" if conviction >= self.decision_threshold else "WAIT",
                "confidence": self._calculate_confidence(analytic_score, predictive_prob),
                "analytic_score": analytic_score,
                "predictive_probability": predictive_prob,
                "signal_action": signal_action,
                "reasoning": self._generate_reasoning(analytic_result, predictive_result, conviction),
                "risk_level": self._assess_risk(conviction)
            }

            logger.info(f"Ensemble decision: {conviction:.3f} conviction - {decision['decision']} ({decision['risk_level']})")
            return decision

        except Exception as e:
            logger.error(f"Error in ensemble decision: {e}")
            return {
                "conviction": 0.5,
                "decision": "WAIT",
                "confidence": "LOW",
                "reasoning": f"Error in decision making: {e}",
                "risk_level": "UNKNOWN"
            }

    def _calculate_confidence(self, analytic_score: float, predictive_prob: float) -> str:
        """Calculate overall confidence level."""
        # High confidence if both engines agree strongly
        agreement = abs(analytic_score - predictive_prob)

        if agreement <= 0.1 and max(analytic_score, predictive_prob) >= 0.8:
            return "VERY_HIGH"
        elif agreement <= 0.2 and max(analytic_score, predictive_prob) >= 0.7:
            return "HIGH"
        elif agreement <= 0.3:
            return "MODERATE"
        elif agreement <= 0.4:
            return "LOW"
        else:
            return "VERY_LOW"

    def _generate_reasoning(self, analytic: Dict, predictive: Dict, conviction: float) -> str:
        """Generate human-readable reasoning for the decision."""
        reasons = []

        # Analytic reasoning
        analytic_score = analytic.get("overall_score", 0.5)
        if analytic_score >= 0.8:
            reasons.append("Excellent market structure")
        elif analytic_score >= 0.6:
            reasons.append("Good market structure")
        elif analytic_score < 0.4:
            reasons.append("Poor market structure")

        # Predictive reasoning
        pred_prob = predictive.get("probability", 0.5)
        pred_strength = predictive.get("strength", "NEUTRAL")

        if "VERY_STRONG" in pred_strength:
            reasons.append(f"Very strong ML signal ({pred_strength})")
        elif "STRONG" in pred_strength:
            reasons.append(f"Strong ML signal ({pred_strength})")
        elif "MODERATE" in pred_strength:
            reasons.append(f"Moderate ML signal ({pred_strength})")
        else:
            reasons.append("Weak or neutral ML signal")

        # Conviction reasoning
        if conviction >= 0.8:
            reasons.append("High overall conviction - strong trade")
        elif conviction >= 0.75:
            reasons.append("Moderate conviction - acceptable trade")
        elif conviction >= 0.6:
            reasons.append("Low conviction - monitor closely")
        else:
            reasons.append("Very low conviction - avoid trade")

        return " | ".join(reasons)

    def _assess_risk(self, conviction: float) -> str:
        """Assess risk level based on conviction."""
        if conviction >= 0.85:
            return "LOW_RISK"
        elif conviction >= 0.8:
            return "MODERATE_RISK"
        elif conviction >= 0.75:
            return "HIGH_RISK"
        else:
            return "VERY_HIGH_RISK"

    def adjust_weights(self, analytic_weight: float, predictive_weight: float):
        """Dynamically adjust engine weights."""
        self.analytic_weight = analytic_weight
        self.predictive_weight = predictive_weight

        # Normalize
        total = analytic_weight + predictive_weight
        self.analytic_weight /= total
        self.predictive_weight /= total

        logger.info(f"Adjusted weights - Analytic: {self.analytic_weight:.2f}, Predictive: {self.predictive_weight:.2f}")

    def get_war_room_status(self) -> Dict:
        """Get current War Room status."""
        return {
            "analytic_weight": self.analytic_weight,
            "predictive_weight": self.predictive_weight,
            "decision_threshold": 0.75,
            "status": "ACTIVE"
        }