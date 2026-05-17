"""Backtesting engine, fill assumptions, and metrics."""

from trading_bot.backtest.engine import BacktestEngine, BacktestResult
from trading_bot.backtest.fills import FillAssumption, estimate_fill_price
from trading_bot.backtest.metrics import BacktestMetrics, BacktestTrade, calculate_metrics
from trading_bot.backtest.slippage import apply_slippage

__all__ = [
    "BacktestEngine",
    "BacktestMetrics",
    "BacktestResult",
    "BacktestTrade",
    "FillAssumption",
    "apply_slippage",
    "calculate_metrics",
    "estimate_fill_price",
]
