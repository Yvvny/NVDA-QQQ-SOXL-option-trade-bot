from datetime import date

from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.regime import RegimeLabel
from trading_bot.strategies.spec_compliance import validate_candidate_against_strategy_spec


def test_spec_gate_approves_compliant_put_credit_spread():
    candidate = StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="QQQ",
        legs=(
            OptionLeg(_contract("put", 450, -0.25, 0.49, 0.51), OptionAction.SELL),
            OptionLeg(_contract("put", 449, -0.10, 0.24, 0.26), OptionAction.BUY),
        ),
        dte=30,
        entry_score=80,
        max_profit=25,
        max_loss=75,
        expected_credit_or_debit=25,
        reason_codes=("regime_fit_preferred", "volatility_missing"),
        exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.5),
    )

    decision = validate_candidate_against_strategy_spec(
        candidate,
        regime_label=RegimeLabel.BULL_TREND_HIGH_IV,
        account_equity=2000,
    )

    assert decision.approved is True
    assert "spec_iv_rank_or_iv_percentile_missing" in decision.warnings


def test_spec_gate_rejects_soxl_short_premium():
    candidate = StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="SOXL",
        legs=(
            OptionLeg(
                _contract("put", 30, -0.25, 0.49, 0.51, underlying="SOXL"), OptionAction.SELL
            ),
            OptionLeg(_contract("put", 29, -0.10, 0.24, 0.26, underlying="SOXL"), OptionAction.BUY),
        ),
        dte=30,
        entry_score=80,
        max_profit=25,
        max_loss=75,
        expected_credit_or_debit=25,
        reason_codes=("regime_fit_preferred",),
        exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.5),
    )

    decision = validate_candidate_against_strategy_spec(
        candidate,
        regime_label=RegimeLabel.BULL_TREND_HIGH_IV,
        account_equity=2000,
    )

    assert decision.approved is False
    assert "spec_soxl_short_premium_not_allowed_v1" in decision.reason_codes


def _contract(
    option_type: str,
    strike: float,
    delta: float,
    bid: float,
    ask: float,
    *,
    underlying: str = "QQQ",
) -> OptionContract:
    return OptionContract(
        symbol=f"{underlying} 2026-06-19 {strike:g} {option_type}",
        underlying=underlying,
        expiration=date(2026, 6, 19),
        strike=strike,
        option_type=OptionType(option_type),
        bid=bid,
        ask=ask,
        mid=(bid + ask) / 2,
        delta=delta,
        volume=100,
        open_interest=1000,
    )
