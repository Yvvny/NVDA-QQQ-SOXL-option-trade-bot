from datetime import date

from trading_bot.backtest import (
    DEFAULT_EXIT_VARIANTS,
    BacktestEngine,
    BacktestTrade,
    calculate_metrics,
    estimate_fill_price,
    run_exit_matrix,
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


def test_backtest_scenarios_apply_risk_fills_exit_rules_and_fees():
    from trading_bot.backtest import (
        BacktestScenario,
        BacktestSimulationConfig,
        FillAssumption,
        OptionPositionSnapshot,
    )
    from trading_bot.core.enums import OptionAction, OptionType
    from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate

    expiration = date(2026, 6, 19)
    short_put = OptionContract(
        symbol="QQQ 2026-06-19 450 put",
        underlying="QQQ",
        expiration=expiration,
        strike=450,
        option_type=OptionType.PUT,
        bid=0.45,
        ask=0.55,
        mid=0.50,
        delta=-0.25,
        volume=100,
        open_interest=1000,
    )
    long_put = OptionContract(
        symbol="QQQ 2026-06-19 449 put",
        underlying="QQQ",
        expiration=expiration,
        strike=449,
        option_type=OptionType.PUT,
        bid=0.20,
        ask=0.30,
        mid=0.25,
        delta=-0.10,
        volume=100,
        open_interest=1000,
    )
    candidate = StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="QQQ",
        legs=(
            OptionLeg(short_put, OptionAction.SELL),
            OptionLeg(long_put, OptionAction.BUY),
        ),
        dte=30,
        entry_score=80,
        max_profit=50,
        max_loss=50,
        expected_credit_or_debit=50,
        reason_codes=("fixture",),
        exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.5),
    )

    result = BacktestEngine(
        initial_equity=2000,
        simulation_config=BacktestSimulationConfig(
            fill_assumption=FillAssumption(bid_ask_spread=0, slippage=0),
            commission_per_contract=0,
        ),
    ).run_scenarios(
        [
            BacktestScenario(
                trade_id="bt1",
                candidate=candidate,
                entry_date=date(2026, 1, 1),
                exit_snapshots=(
                    OptionPositionSnapshot(date(2026, 1, 5), dte=26, mark_price=0.20),
                ),
            )
        ]
    )

    assert result.metrics.number_of_trades == 1
    assert result.trades[0].pnl == 30
    assert result.trades[0].exit_reason == "profit_target"
    assert result.skipped_trades == ()


def test_backtest_scenarios_scale_pnl_and_risk_for_quantity():
    from trading_bot.backtest import (
        BacktestScenario,
        BacktestSimulationConfig,
        FillAssumption,
        OptionPositionSnapshot,
    )
    from trading_bot.core.enums import OptionAction, OptionType
    from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate

    expiration = date(2026, 6, 19)
    short_put = OptionContract(
        symbol="QQQ 2026-06-19 450 put",
        underlying="QQQ",
        expiration=expiration,
        strike=450,
        option_type=OptionType.PUT,
        bid=0.45,
        ask=0.55,
        mid=0.50,
        delta=-0.25,
        volume=100,
        open_interest=1000,
    )
    long_put = OptionContract(
        symbol="QQQ 2026-06-19 449 put",
        underlying="QQQ",
        expiration=expiration,
        strike=449,
        option_type=OptionType.PUT,
        bid=0.20,
        ask=0.30,
        mid=0.25,
        delta=-0.10,
        volume=100,
        open_interest=1000,
    )
    candidate = StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="QQQ",
        legs=(
            OptionLeg(short_put, OptionAction.SELL),
            OptionLeg(long_put, OptionAction.BUY),
        ),
        dte=30,
        entry_score=80,
        max_profit=50,
        max_loss=50,
        expected_credit_or_debit=50,
        reason_codes=("fixture",),
        exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.5),
        quantity=2,
    )

    result = BacktestEngine(
        initial_equity=2000,
        simulation_config=BacktestSimulationConfig(
            fill_assumption=FillAssumption(bid_ask_spread=0, slippage=0),
            commission_per_contract=0,
        ),
    ).run_scenarios(
        [
            BacktestScenario(
                trade_id="bt_qty",
                candidate=candidate,
                entry_date=date(2026, 1, 1),
                exit_snapshots=(
                    OptionPositionSnapshot(date(2026, 1, 5), dte=26, mark_price=0.20),
                ),
            )
        ]
    )

    assert result.metrics.number_of_trades == 1
    assert result.trades[0].pnl == 60
    assert result.trades[0].max_loss == 100


def test_backtest_scenarios_skip_trades_rejected_by_risk_engine():
    from trading_bot.backtest import BacktestScenario, OptionPositionSnapshot
    from trading_bot.core.enums import OptionAction, OptionType
    from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate

    expiration = date(2026, 6, 19)
    short_put = OptionContract(
        symbol="QQQ 2026-06-19 450 put",
        underlying="QQQ",
        expiration=expiration,
        strike=450,
        option_type=OptionType.PUT,
        bid=0.45,
        ask=0.55,
        mid=0.50,
        delta=-0.25,
        volume=100,
        open_interest=1000,
    )
    candidate = StrategyCandidate(
        strategy_name="naked_short_put",
        underlying="QQQ",
        legs=(OptionLeg(short_put, OptionAction.SELL),),
        dte=30,
        entry_score=80,
        max_profit=50,
        max_loss=100,
        expected_credit_or_debit=50,
        reason_codes=("fixture",),
        exit_plan=ExitPlan(profit_target_pct=0.5),
    )

    result = BacktestEngine(initial_equity=2000).run_scenarios(
        [
            BacktestScenario(
                trade_id="bt_rejected",
                candidate=candidate,
                entry_date=date(2026, 1, 1),
                exit_snapshots=(
                    OptionPositionSnapshot(date(2026, 1, 5), dte=26, mark_price=0.20),
                ),
            )
        ]
    )

    assert result.trades == ()
    assert "naked_short_option_forbidden" in result.skipped_trades[0].reason_codes


def test_exit_matrix_runs_all_documented_variants_and_changes_exit_outcomes():
    from trading_bot.backtest import BacktestScenario, OptionPositionSnapshot

    candidate = _put_credit_candidate(
        expected_credit_or_debit=50,
        max_profit=50,
        max_loss=50,
        quantity=1,
    )
    scenarios = [
        BacktestScenario(
            trade_id="matrix_1",
            candidate=candidate,
            entry_date=date(2026, 1, 1),
            exit_snapshots=(
                OptionPositionSnapshot(date(2026, 1, 5), dte=26, mark_price=0.25),
                OptionPositionSnapshot(date(2026, 1, 8), dte=23, mark_price=0.21),
            ),
        )
    ]

    report = run_exit_matrix(scenarios, initial_equity=2000)

    assert [variant.code for variant in report.variants] == [
        variant.code for variant in DEFAULT_EXIT_VARIANTS
    ]
    exit_reasons = {variant.code: variant.result.trades[0].exit_reason for variant in report.variants}
    assert exit_reasons["E1"] == "profit_target"
    assert exit_reasons["E2"] == "profit_target"
    assert exit_reasons["E3"] == "last_snapshot"
    assert exit_reasons["E4"] == "profit_target"


def _put_credit_candidate(
    *,
    expected_credit_or_debit: float,
    max_profit: float,
    max_loss: float,
    quantity: int = 1,
):
    from trading_bot.core.enums import OptionAction, OptionType
    from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate

    expiration = date(2026, 6, 19)
    short_put = OptionContract(
        symbol="QQQ 2026-06-19 450 put",
        underlying="QQQ",
        expiration=expiration,
        strike=450,
        option_type=OptionType.PUT,
        bid=0.45,
        ask=0.55,
        mid=0.50,
        delta=-0.25,
        volume=100,
        open_interest=1000,
    )
    long_put = OptionContract(
        symbol="QQQ 2026-06-19 449 put",
        underlying="QQQ",
        expiration=expiration,
        strike=449,
        option_type=OptionType.PUT,
        bid=0.20,
        ask=0.30,
        mid=0.25,
        delta=-0.10,
        volume=100,
        open_interest=1000,
    )
    return StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="QQQ",
        legs=(
            OptionLeg(short_put, OptionAction.SELL),
            OptionLeg(long_put, OptionAction.BUY),
        ),
        dte=30,
        entry_score=80,
        max_profit=max_profit,
        max_loss=max_loss,
        expected_credit_or_debit=expected_credit_or_debit,
        reason_codes=("fixture",),
        exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.5),
        quantity=quantity,
    )
