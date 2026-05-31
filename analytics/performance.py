"""Performance analytics for replay/backtest trade results."""

from __future__ import annotations

import math
from typing import Iterable

import pandas as pd


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_peak = equity.cummax()
    drawdown = equity - running_peak
    return float(drawdown.min())


def _max_consecutive_losses(profits: Iterable[float]) -> int:
    worst = 0
    current = 0
    for profit in profits:
        if profit < 0:
            current += 1
            worst = max(worst, current)
        else:
            current = 0
    return worst


def summarize_performance(trades, equity_curve=None, risk_free_rate: float = 0.0) -> dict:
    """Return core performance metrics for a list/dataframe of closed trades."""
    trades_df = trades.copy() if isinstance(trades, pd.DataFrame) else pd.DataFrame(trades or [])
    equity_df = equity_curve.copy() if isinstance(equity_curve, pd.DataFrame) else pd.DataFrame(equity_curve or [])

    if trades_df.empty:
        return {
            "net_profit": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "expectancy": 0.0,
            "sharpe_ratio": 0.0,
            "average_r_multiple": 0.0,
            "consecutive_losses": 0,
            "total_trades": 0,
            "long_short_breakdown": {
                "long": {"trades": 0, "net_profit": 0.0, "win_rate": 0.0},
                "short": {"trades": 0, "net_profit": 0.0, "win_rate": 0.0},
            },
        }

    profits = pd.to_numeric(trades_df.get("profit", 0), errors="coerce").fillna(0.0)
    wins = profits[profits > 0]
    losses = profits[profits < 0]
    total_trades = int(len(trades_df))
    gross_profit = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    win_rate = float(len(wins) / total_trades) if total_trades else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (None if gross_profit > 0 else 0.0)
    expectancy = float(profits.mean()) if total_trades else 0.0

    r_values = pd.to_numeric(trades_df.get("r_multiple", 0), errors="coerce").dropna()
    average_r = float(r_values.mean()) if not r_values.empty else 0.0

    if not equity_df.empty and "equity" in equity_df:
        equity = pd.to_numeric(equity_df["equity"], errors="coerce").dropna()
        max_dd = _max_drawdown(equity)
        returns = equity.pct_change().replace([math.inf, -math.inf], pd.NA).dropna()
        if len(returns) > 1 and float(returns.std()) > 0:
            sharpe = float((returns.mean() - risk_free_rate) / returns.std() * math.sqrt(252))
        else:
            sharpe = 0.0
    else:
        max_dd = 0.0
        sharpe = 0.0

    breakdown = {}
    for action, label in [("BUY", "long"), ("SELL", "short")]:
        side = trades_df[trades_df.get("action", "").astype(str).str.upper() == action]
        side_profit = pd.to_numeric(side.get("profit", 0), errors="coerce").fillna(0.0)
        side_wins = side_profit[side_profit > 0]
        breakdown[label] = {
            "trades": int(len(side)),
            "net_profit": float(side_profit.sum()) if len(side_profit) else 0.0,
            "win_rate": float(len(side_wins) / len(side)) if len(side) else 0.0,
        }

    return {
        "net_profit": float(profits.sum()),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown": max_dd,
        "expectancy": expectancy,
        "sharpe_ratio": sharpe,
        "average_r_multiple": average_r,
        "consecutive_losses": _max_consecutive_losses(profits.tolist()),
        "total_trades": total_trades,
        "long_short_breakdown": breakdown,
    }
