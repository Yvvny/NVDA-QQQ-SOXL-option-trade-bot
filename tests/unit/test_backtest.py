from datetime import date

from trading_bot.backtest import (
    BacktestEngine,
    BacktestTrade,
    calculate_metrics,
    estimate_fill_price,
)


def test_backtest_metrics_include_drawdown_profit_factor_and_expectancy():
    trades = [
        _trade("t1", date(2026, 1, 2), 60),
        _trade("t2", date(2026, 1, 3), -40),
        _trade("t3", date(2026, 1, 4), -30),
        _trade("t4", date(2026, 1, 5), 90),
    ]

    metrics = calculate_metrics(trades, initial_equity=2000)

    assert metrics.number_of_trades == 4
    assert metrics.ending_equity == 2080
    assert metrics.total_return == 0.04
    assert metrics.max_drawdown > 0
    assert metrics.profit_factor == (150 / 70)
    assert metrics.win_rate == 0.5
    assert metrics.expectancy_per_trade == 20
    assert metrics.consecutive_losses == 2
    assert metrics.worst_trade == -40


def test_backtest_engine_returns_trade_tuple_and_metrics():
    result = BacktestEngine(initial_equity=2000).run_from_trade_results(
        [_trade("t1", date(2026, 1, 2), 50)]
    )

    assert result.metrics.ending_equity == 2050
    assert len(result.trades) == 1


def test_fill_estimate_penalizes_buy_and_sell():
    assert estimate_fill_price(1.00, "buy") == 1.06
    assert estimate_fill_price(1.00, "sell") == 0.94


def _trade(trade_id: str, exit_date: date, pnl: float) -> BacktestTrade:
    return BacktestTrade(
        trade_id=trade_id,
        symbol="QQQ",
        strategy_name="put_credit_spread",
        entry_date=date(2026, 1, 1),
        exit_date=exit_date,
        pnl=pnl,
        max_loss=100,
    )
