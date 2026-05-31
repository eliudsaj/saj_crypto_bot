"""
Measure realized strategy edge from local trade logs.

This script uses TRADE_CLOSED records from logs/trades_*.json. Flat closes
are treated as cancellations/expired orders by default and excluded from the
edge calculation.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import random
import statistics
from dataclasses import dataclass


@dataclass
class ClosedTrade:
    date: str
    symbol: str
    profit: float
    risk: float
    r: float | None
    reason: str


def read_closed_trades(log_dir: pathlib.Path) -> list[ClosedTrade]:
    trades: list[ClosedTrade] = []
    for path in sorted(log_dir.glob("trades_*.json")):
        if ".corrupt-" in path.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Skipping unreadable log {path.name}: {exc}")
            continue
        if not isinstance(data, list):
            continue
        date = path.stem.replace("trades_", "")
        for entry in data:
            if entry.get("event") != "TRADE_CLOSED":
                continue
            profit = float(entry.get("profit") or 0.0)
            risk = float(entry.get("risk") or 0.0)
            raw_r = entry.get("r")
            try:
                r_value = float(raw_r) if raw_r is not None else profit / risk if risk > 0 else None
            except Exception:
                r_value = None
            trades.append(
                ClosedTrade(
                    date=date,
                    symbol=str(entry.get("symbol") or "?"),
                    profit=profit,
                    risk=risk,
                    r=r_value if r_value is not None and math.isfinite(r_value) else None,
                    reason=str(entry.get("reason") or ""),
                )
            )
    return trades


def bootstrap_mean_ci(values: list[float], samples: int = 20_000, seed: int = 42) -> tuple[float, float, float] | None:
    if not values:
        return None
    random.seed(seed)
    length = len(values)
    means = [
        sum(values[random.randrange(length)] for _ in range(length)) / length
        for _ in range(samples)
    ]
    means.sort()
    return means[int(samples * 0.025)], means[int(samples * 0.5)], means[int(samples * 0.975)]


def max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def summarize(trades: list[ClosedTrade], value_name: str = "profit") -> dict:
    values = [
        getattr(trade, value_name)
        for trade in trades
        if getattr(trade, value_name) is not None and math.isfinite(getattr(trade, value_name))
    ]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    count = len(values)
    mean = sum(values) / count if count else 0.0
    sd = statistics.stdev(values) if count > 1 else 0.0
    se = sd / math.sqrt(count) if count > 1 else 0.0
    return {
        "n": count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / count if count else None,
        "net": sum(values),
        "average": mean,
        "median": statistics.median(values) if count else None,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "profit_factor": gross_win / gross_loss if gross_loss else None,
        "max_drawdown": max_drawdown(values),
        "t_stat": mean / se if se else None,
        "bootstrap_mean_ci": bootstrap_mean_ci(values),
    }


def format_number(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{decimals}f}"


def print_summary(name: str, trades: list[ClosedTrade]) -> None:
    stats = summarize(trades, "profit")
    ci = stats["bootstrap_mean_ci"]
    print(f"\n{name}")
    print("-" * len(name))
    print(f"trades: {stats['n']}")
    print(f"wins/losses: {stats['wins']}/{stats['losses']}")
    print(f"win rate: {format_number((stats['win_rate'] or 0) * 100)}%")
    print(f"net P&L: {format_number(stats['net'])}")
    print(f"average/trade: {format_number(stats['average'])}")
    print(f"median/trade: {format_number(stats['median'])}")
    print(f"profit factor: {format_number(stats['profit_factor'], 3)}")
    print(f"max drawdown: {format_number(stats['max_drawdown'])}")
    print(f"t-stat of mean: {format_number(stats['t_stat'], 3)}")
    if ci:
        print(f"bootstrap average/trade 95% CI: {format_number(ci[0])} to {format_number(ci[2])}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure realized trading edge from JSON logs.")
    parser.add_argument("--log-dir", default="logs", help="Directory containing trades_*.json files.")
    parser.add_argument("--include-flat", action="store_true", help="Include zero-profit close records.")
    parser.add_argument("--since", help="Only include trades on or after YYYY-MM-DD.")
    parser.add_argument("--exclude-date", action="append", default=[], help="Exclude a specific YYYY-MM-DD. Can be repeated.")
    args = parser.parse_args()

    all_closed = read_closed_trades(pathlib.Path(args.log_dir))
    filtered = all_closed
    if not args.include_flat:
        filtered = [trade for trade in filtered if abs(trade.profit) > 1e-9]
    if args.since:
        filtered = [trade for trade in filtered if trade.date >= args.since]
    if args.exclude_date:
        excluded = set(args.exclude_date)
        filtered = [trade for trade in filtered if trade.date not in excluded]

    print(f"closed records: {len(all_closed)}")
    print(f"records used for edge: {len(filtered)}")
    print(f"flat/cancelled records: {sum(1 for trade in all_closed if abs(trade.profit) <= 1e-9)}")

    print_summary("Realized P&L Edge", filtered)

    trades_with_r = [trade for trade in filtered if trade.r is not None]
    if trades_with_r:
        r_stats = summarize(trades_with_r, "r")
        ci = r_stats["bootstrap_mean_ci"]
        print("\nR-Multiple Edge")
        print("---------------")
        print(f"trades with R: {r_stats['n']}")
        print(f"average R/trade: {format_number(r_stats['average'], 3)}")
        print(f"median R/trade: {format_number(r_stats['median'], 3)}")
        print(f"R profit factor: {format_number(r_stats['profit_factor'], 3)}")
        if ci:
            print(f"bootstrap average R 95% CI: {format_number(ci[0], 3)} to {format_number(ci[2], 3)}")

    print("\nBy Symbol")
    print("---------")
    by_symbol: dict[str, list[ClosedTrade]] = collections.defaultdict(list)
    for trade in filtered:
        by_symbol[trade.symbol].append(trade)
    for symbol, symbol_trades in sorted(by_symbol.items()):
        stats = summarize(symbol_trades, "profit")
        print(
            f"{symbol:8} n={stats['n']:3} net={stats['net']:10.2f} "
            f"avg={stats['average']:8.2f} win={((stats['win_rate'] or 0) * 100):5.1f}% "
            f"pf={format_number(stats['profit_factor'], 3)}"
        )

    print("\nBy Date")
    print("-------")
    by_date: dict[str, list[ClosedTrade]] = collections.defaultdict(list)
    for trade in filtered:
        by_date[trade.date].append(trade)
    for date, date_trades in sorted(by_date.items()):
        stats = summarize(date_trades, "profit")
        print(
            f"{date} n={stats['n']:3} net={stats['net']:10.2f} "
            f"avg={stats['average']:8.2f} win={((stats['win_rate'] or 0) * 100):5.1f}% "
            f"pf={format_number(stats['profit_factor'], 3)}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
