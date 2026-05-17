from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class BacktestTrade:
    trade_id: str
    symbol: str
    strategy_name: str
    entry_date: date
    exit_date: date
    pnl: float
    max_loss: float


@dataclass(frozen=True)
class BacktestMetrics:
    initial_equity: float
    ending_equity: float
    total_return: float
    max_drawdown: float
    profit_factor: float | None
    win_rate: float
    average_win: float | None
    average_loss: float | None
    average_win_loss_ratio: float | None
    expectancy_per_trade: float
    sharpe: float | None
    sortino: float | None
    calmar: float | None
    exposure_time_trades: int
    number_of_trades: int
    consecutive_losses: int
    worst_trade: float | None
    worst_day: float | None
    worst_week: float | None


def calculate_metrics(trades: list[BacktestTrade], initial_equity: float) -> BacktestMetrics:
    if initial_equity <= 0:
        raise ValueError("Initial equity must be positive.")

    equity = initial_equity
    peak_equity = initial_equity
    max_drawdown = 0.0
    wins: list[float] = []
    losses: list[float] = []
    trade_returns: list[float] = []
    daily_pnl: dict[date, float] = defaultdict(float)
    weekly_pnl: dict[tuple[int, int], float] = defaultdict(float)
    current_loss_streak = 0
    max_loss_streak = 0

    for trade in sorted(trades, key=lambda item: item.exit_date):
        equity_before = equity
        equity += trade.pnl
        peak_equity = max(peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity if peak_equity else 0.0
        max_drawdown = max(max_drawdown, drawdown)
        trade_returns.append(trade.pnl / equity_before)
        daily_pnl[trade.exit_date] += trade.pnl
        iso_year, iso_week, _ = trade.exit_date.isocalendar()
        weekly_pnl[(iso_year, iso_week)] += trade.pnl

        if trade.pnl > 0:
            wins.append(trade.pnl)
            current_loss_streak = 0
        elif trade.pnl < 0:
            losses.append(trade.pnl)
            current_loss_streak += 1
            max_loss_streak = max(max_loss_streak, current_loss_streak)

    total_profit = sum(wins)
    total_loss_abs = abs(sum(losses))
    profit_factor = None if total_loss_abs == 0 else total_profit / total_loss_abs
    average_win = _mean(wins)
    average_loss = _mean(losses)
    average_win_loss_ratio = (
        None
        if average_win is None or average_loss is None or average_loss == 0
        else average_win / abs(average_loss)
    )
    ending_equity = equity
    total_return = (ending_equity - initial_equity) / initial_equity
    sharpe = _sharpe(trade_returns)
    sortino = _sortino(trade_returns)
    calmar = None if max_drawdown == 0 else total_return / max_drawdown

    return BacktestMetrics(
        initial_equity=initial_equity,
        ending_equity=round(ending_equity, 2),
        total_return=total_return,
        max_drawdown=max_drawdown,
        profit_factor=profit_factor,
        win_rate=len(wins) / len(trades) if trades else 0.0,
        average_win=average_win,
        average_loss=average_loss,
        average_win_loss_ratio=average_win_loss_ratio,
        expectancy_per_trade=sum(trade.pnl for trade in trades) / len(trades) if trades else 0.0,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        exposure_time_trades=len(trades),
        number_of_trades=len(trades),
        consecutive_losses=max_loss_streak,
        worst_trade=min((trade.pnl for trade in trades), default=None),
        worst_day=min(daily_pnl.values(), default=None),
        worst_week=min(weekly_pnl.values(), default=None),
    )


def _mean(values: list[float]) -> float | None:
    return None if not values else sum(values) / len(values)


def _sharpe(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    mean_return = sum(returns) / len(returns)
    std_dev = _sample_std(returns)
    if std_dev == 0:
        return None
    return mean_return / std_dev * math.sqrt(len(returns))


def _sortino(returns: list[float]) -> float | None:
    downside = [value for value in returns if value < 0]
    if len(downside) < 2:
        return None
    mean_return = sum(returns) / len(returns)
    downside_std = _sample_std(downside)
    if downside_std == 0:
        return None
    return mean_return / downside_std * math.sqrt(len(returns))


def _sample_std(values: list[float]) -> float:
    mean_value = sum(values) / len(values)
    variance = sum((value - mean_value) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)
