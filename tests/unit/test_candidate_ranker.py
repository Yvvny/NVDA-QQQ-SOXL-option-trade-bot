from dataclasses import replace
from datetime import date

from trading_bot.config.settings import load_settings
from trading_bot.core.enums import OptionAction, OptionType, OrderType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.risk.portfolio import OpenPosition, PortfolioState
from trading_bot.strategies.ranker import CandidateRanker


def test_candidate_ranker_rejects_missing_max_loss_before_scoring():
    selected = CandidateRanker(load_settings(env={})).select(
        [_candidate(max_loss=None)],
        risk_budget_base=2000,
        portfolio_state=PortfolioState(account_equity=2000),
    )

    assert selected == []


def test_candidate_ranker_rejects_market_order_before_scoring():
    selected = CandidateRanker(load_settings(env={})).select(
        [_candidate(order_type=OrderType.MARKET)],
        risk_budget_base=2000,
        portfolio_state=PortfolioState(account_equity=2000),
    )

    assert selected == []


def test_candidate_ranker_returns_no_trade_when_top_and_runner_up_are_too_close():
    settings = load_settings(env={})
    selected = CandidateRanker(settings).select(
        [
            _candidate(entry_score=82, max_loss=50),
            _candidate(entry_score=81, max_loss=50, strike=455),
        ],
        risk_budget_base=2000,
        portfolio_state=PortfolioState(account_equity=2000),
    )

    assert selected == []


def test_candidate_ranker_allows_close_score_when_top_has_much_lower_max_loss():
    selected = CandidateRanker(load_settings(env={})).select(
        [
            _candidate(entry_score=82, max_loss=30),
            _candidate(entry_score=81, max_loss=50, strike=455),
        ],
        risk_budget_base=2000,
        portfolio_state=PortfolioState(account_equity=2000),
    )

    assert len(selected) == 1
    assert selected[0].max_loss == 30


def test_candidate_ranker_raises_threshold_in_preservation_mode():
    settings = load_settings(env={})
    settings = replace(
        settings,
        selection=replace(settings.selection, normal_min_opportunity_score=60),
    )
    selected = CandidateRanker(settings).select(
        [_candidate(entry_score=65, max_loss=50)],
        risk_budget_base=1700,
        portfolio_state=PortfolioState(account_equity=1705.5),
    )

    assert selected == []


def test_candidate_ranker_penalizes_existing_symbol_exposure():
    settings = load_settings(env={})
    settings = replace(
        settings,
        selection=replace(settings.selection, min_top_score_gap=1),
    )
    portfolio = PortfolioState(
        account_equity=2000,
        open_positions=(OpenPosition("NVDA", "call_debit_spread", 50),),
    )

    selected = CandidateRanker(settings).select(
        [
            _candidate(underlying="NVDA", entry_score=80, max_loss=50),
            _candidate(underlying="QQQ", entry_score=80, max_loss=50, strike=455),
        ],
        risk_budget_base=2000,
        portfolio_state=portfolio,
    )

    assert len(selected) == 1
    assert selected[0].underlying == "QQQ"


def _candidate(
    *,
    underlying: str = "QQQ",
    entry_score: float = 85,
    max_loss: float | None = 50,
    order_type: OrderType = OrderType.LIMIT,
    strike: float = 450,
) -> StrategyCandidate:
    expiration = date(2026, 6, 19)
    return StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying=underlying,
        legs=(
            OptionLeg(
                contract=OptionContract(
                    symbol=f"{underlying} {expiration.isoformat()} {strike} put",
                    underlying=underlying,
                    expiration=expiration,
                    strike=strike,
                    option_type=OptionType.PUT,
                    bid=1.00,
                    ask=1.03,
                    mid=1.015,
                    delta=-0.20,
                    volume=100,
                    open_interest=1000,
                ),
                action=OptionAction.SELL,
            ),
            OptionLeg(
                contract=OptionContract(
                    symbol=f"{underlying} {expiration.isoformat()} {strike - 1} put",
                    underlying=underlying,
                    expiration=expiration,
                    strike=strike - 1,
                    option_type=OptionType.PUT,
                    bid=0.45,
                    ask=0.48,
                    mid=0.465,
                    delta=-0.10,
                    volume=100,
                    open_interest=1000,
                ),
                action=OptionAction.BUY,
            ),
        ),
        dte=30,
        entry_score=entry_score,
        max_profit=50,
        max_loss=max_loss,
        expected_credit_or_debit=50,
        reason_codes=("fixture",),
        exit_plan=ExitPlan(profit_target_pct=0.50, stop_loss_multiple=2.0),
        order_type=order_type,
    )
