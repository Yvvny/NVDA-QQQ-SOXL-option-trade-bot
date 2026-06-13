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
        risk_budget_base=2000,
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
        risk_budget_base=2000,
    )

    assert decision.approved is False
    assert "spec_soxl_short_premium_not_allowed_v1" in decision.reason_codes


def test_spec_gate_warns_instead_of_rejecting_missing_activity_metadata_for_real_data():
    candidate = StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="QQQ",
        legs=(
            OptionLeg(
                _contract(
                    "put",
                    450,
                    -0.25,
                    0.49,
                    0.51,
                    volume=None,
                    open_interest=None,
                    allow_missing_activity_data=True,
                ),
                OptionAction.SELL,
            ),
            OptionLeg(
                _contract(
                    "put",
                    449,
                    -0.10,
                    0.24,
                    0.26,
                    volume=None,
                    open_interest=None,
                    allow_missing_activity_data=True,
                ),
                OptionAction.BUY,
            ),
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
        risk_budget_base=2000,
    )

    assert decision.approved is True
    assert "spec_missing_volume_metadata" in decision.warnings
    assert "spec_missing_open_interest_metadata" in decision.warnings
    assert "spec_low_or_missing_volume" not in decision.reason_codes
    assert "spec_low_or_missing_open_interest" not in decision.reason_codes


def test_spec_gate_allows_experimental_range_low_iv_call_debit_in_paper_mode():
    candidate = StrategyCandidate(
        strategy_name="call_debit_spread",
        underlying="NVDA",
        legs=(
            OptionLeg(
                _contract(
                    "call",
                    220,
                    0.50,
                    8.45,
                    8.55,
                    volume=None,
                    open_interest=None,
                    allow_missing_activity_data=True,
                ),
                OptionAction.BUY,
            ),
            OptionLeg(
                _contract(
                    "call",
                    223,
                    0.35,
                    7.15,
                    7.25,
                    volume=None,
                    open_interest=None,
                    allow_missing_activity_data=True,
                ),
                OptionAction.SELL,
            ),
        ),
        dte=29,
        entry_score=66,
        max_profit=180,
        max_loss=120,
        expected_credit_or_debit=120,
        reason_codes=("regime_fit_reduced",),
        exit_plan=ExitPlan(profit_target_pct=0.75, stop_loss_pct=0.45),
    )

    strict_decision = validate_candidate_against_strategy_spec(
        candidate,
        regime_label=RegimeLabel.RANGE_LOW_IV,
        risk_budget_base=2000,
    )
    experimental_decision = validate_candidate_against_strategy_spec(
        candidate,
        regime_label=RegimeLabel.RANGE_LOW_IV,
        risk_budget_base=2000,
        experimental_mode=True,
    )

    assert strict_decision.approved is False
    assert "spec_strategy_not_allowed_for_range_low_iv" in strict_decision.reason_codes
    assert experimental_decision.approved is True
    assert "spec_strategy_not_allowed_for_range_low_iv" not in experimental_decision.reason_codes
    assert (
        "spec_experimental_strategy_override_for_range_low_iv"
        in experimental_decision.warnings
    )


def test_spec_gate_rejects_normal_trade_above_20pct_equity():
    candidate = StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="QQQ",
        legs=(
            OptionLeg(_contract("put", 450, -0.25, 0.49, 0.51), OptionAction.SELL),
            OptionLeg(_contract("put", 448, -0.10, 0.14, 0.16), OptionAction.BUY),
        ),
        dte=30,
        entry_score=70,
        max_profit=70,
        max_loss=450,
        expected_credit_or_debit=70,
        reason_codes=("regime_fit_preferred",),
        exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.5),
    )

    decision = validate_candidate_against_strategy_spec(
        candidate,
        regime_label=RegimeLabel.BULL_TREND_HIGH_IV,
        risk_budget_base=2000,
    )

    assert decision.approved is False
    assert "spec_normal_trade_risk_above_20pct_equity" in decision.reason_codes


def test_spec_gate_rejects_high_score_trade_above_40pct_equity():
    candidate = StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="QQQ",
        legs=(
            OptionLeg(_contract("put", 450, -0.25, 3.49, 3.51), OptionAction.SELL),
            OptionLeg(_contract("put", 446, -0.10, 0.49, 0.51), OptionAction.BUY),
        ),
        dte=30,
        entry_score=85,
        max_profit=300,
        max_loss=900,
        expected_credit_or_debit=300,
        reason_codes=("regime_fit_preferred",),
        exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.5),
    )

    decision = validate_candidate_against_strategy_spec(
        candidate,
        regime_label=RegimeLabel.BULL_TREND_HIGH_IV,
        risk_budget_base=2000,
    )

    assert decision.approved is False
    assert "spec_high_score_trade_risk_above_40pct_equity" in decision.reason_codes


def test_spec_gate_rejects_quantity_scaled_trade_above_40pct_equity():
    candidate = StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="QQQ",
        legs=(
            OptionLeg(_contract("put", 450, -0.25, 3.49, 3.51), OptionAction.SELL),
            OptionLeg(_contract("put", 446, -0.10, 0.49, 0.51), OptionAction.BUY),
        ),
        dte=30,
        entry_score=85,
        max_profit=300,
        max_loss=900,
        expected_credit_or_debit=300,
        reason_codes=("regime_fit_preferred",),
        exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.5),
        quantity=2,
    )

    decision = validate_candidate_against_strategy_spec(
        candidate,
        regime_label=RegimeLabel.BULL_TREND_HIGH_IV,
        risk_budget_base=2000,
    )

    assert decision.approved is False
    assert "spec_high_score_trade_risk_above_40pct_equity" in decision.reason_codes


def _contract(
    option_type: str,
    strike: float,
    delta: float,
    bid: float,
    ask: float,
    *,
    underlying: str = "QQQ",
    volume: int | None = 100,
    open_interest: int | None = 1000,
    allow_missing_activity_data: bool = False,
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
        volume=volume,
        open_interest=open_interest,
        allow_missing_activity_data=allow_missing_activity_data,
    )
