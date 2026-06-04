"""Backtesting engine, fill assumptions, and metrics."""

from trading_bot.backtest.engine import (
    BacktestEngine,
    BacktestResult,
    BacktestScenario,
    BacktestSimulationConfig,
    BacktestSkippedTrade,
    OptionPositionSnapshot,
)
from trading_bot.backtest.exit_matrix import (
    DEFAULT_EXIT_VARIANTS,
    ExitMatrixReport,
    ExitMatrixVariantReport,
    ExitVariantSpec,
    load_scenarios_from_json,
    run_exit_matrix,
)
from trading_bot.backtest.fills import FillAssumption, estimate_fill_price
from trading_bot.backtest.metrics import BacktestMetrics, BacktestTrade, calculate_metrics
from trading_bot.backtest.slippage import apply_slippage

__all__ = [
    "BacktestEngine",
    "DEFAULT_EXIT_VARIANTS",
    "ExitMatrixReport",
    "ExitMatrixVariantReport",
    "ExitVariantSpec",
    "BacktestMetrics",
    "BacktestResult",
    "BacktestScenario",
    "BacktestSimulationConfig",
    "BacktestSkippedTrade",
    "BacktestTrade",
    "FillAssumption",
    "OptionPositionSnapshot",
    "apply_slippage",
    "calculate_metrics",
    "estimate_fill_price",
    "load_scenarios_from_json",
    "run_exit_matrix",
]
