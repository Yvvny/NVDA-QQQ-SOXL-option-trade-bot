from __future__ import annotations

from dataclasses import dataclass

from trading_bot.backtest.metrics import BacktestMetrics, BacktestTrade, calculate_metrics


@dataclass(frozen=True)
class BacktestResult:
    metrics: BacktestMetrics
    trades: tuple[BacktestTrade, ...]


class BacktestEngine:
    def __init__(self, initial_equity: float = 2000.0) -> None:
        self.initial_equity = initial_equity

    def run_from_trade_results(self, trades: list[BacktestTrade]) -> BacktestResult:
        return BacktestResult(
            metrics=calculate_metrics(trades, self.initial_equity),
            trades=tuple(trades),
        )
