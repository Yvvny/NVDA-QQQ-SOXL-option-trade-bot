from trading_bot.paper import PaperAccountState, PaperClosedTrade, PaperPosition
from trading_bot.research_bot.attribution import build_paper_strategy_attribution


def test_strategy_attribution_summarizes_closed_and_open_trades():
    state = PaperAccountState(
        open_positions=(
            _position("NVDA", "call_debit_spread", max_loss=180, unrealized_pnl=-60),
        ),
        closed_trades=(
            PaperClosedTrade(
                position=_position("QQQ", "put_credit_spread", max_loss=85),
                closed_at="2026-06-07T10:00:00-04:00",
                exit_reason="stop_loss_multiple",
                realized_pnl=-42.5,
            ),
            PaperClosedTrade(
                position=_position("QQQ", "put_credit_spread", max_loss=100),
                closed_at="2026-06-07T11:00:00-04:00",
                exit_reason="profit_target",
                realized_pnl=50.0,
            ),
        ),
    )

    summaries = build_paper_strategy_attribution(state)
    by_key = {(summary.group, summary.key): summary for summary in summaries}

    qqq = by_key[("symbol", "QQQ")]
    assert qqq.closed_trade_count == 2
    assert qqq.realized_pnl == 7.5
    assert qqq.win_rate == 0.5
    assert qqq.avg_win == 50
    assert qqq.avg_loss == -42.5
    assert qqq.profit_factor == round(50 / 42.5, 4)
    assert qqq.expectancy == 3.75
    assert qqq.stopout_rate == 0.5
    assert qqq.profit_target_hit_rate == 0.5
    assert qqq.capital_efficiency == round(7.5 / 185, 4)

    nvda = by_key[("symbol", "NVDA")]
    assert nvda.open_trade_count == 1
    assert nvda.closed_trade_count == 0
    assert nvda.expectancy is None


def test_strategy_attribution_groups_by_exit_reason():
    state = PaperAccountState(
        closed_trades=(
            PaperClosedTrade(
                position=_position("QQQ", "put_credit_spread", max_loss=85),
                closed_at="2026-06-07T10:00:00-04:00",
                exit_reason="stop_loss_multiple",
                realized_pnl=-42.5,
            ),
        ),
    )

    summaries = build_paper_strategy_attribution(state)
    stopout = next(
        summary
        for summary in summaries
        if summary.group == "exit_reason" and summary.key == "stop_loss_multiple"
    )

    assert stopout.trade_count == 1
    assert stopout.avg_pnl_pct_of_max_loss == -0.5
    assert stopout.stopout_rate == 1.0


def _position(
    symbol: str,
    strategy_name: str,
    *,
    max_loss: float,
    unrealized_pnl: float = 0.0,
) -> PaperPosition:
    return PaperPosition(
        position_id=f"{symbol}-{strategy_name}-{max_loss}",
        opened_at="2026-06-07T09:30:00-04:00",
        underlying=symbol,
        strategy_name=strategy_name,
        dte_at_entry=30,
        entry_score=75,
        max_profit=50,
        max_loss=max_loss,
        expected_credit_or_debit=50,
        price_effect="credit",
        entry_value=-50,
        legs=(),
        exit_plan={},
        unrealized_pnl=unrealized_pnl,
    )
