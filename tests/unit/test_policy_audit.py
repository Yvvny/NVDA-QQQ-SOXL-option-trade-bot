from dataclasses import replace
from datetime import date

from trading_bot.config.settings import load_settings
from trading_bot.core.enums import OptionAction, OptionType, OrderType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.risk.policy_audit import (
    REASON_POLICY_0DTE_FORBIDDEN,
    REASON_POLICY_MARKET_ORDER_FORBIDDEN,
    REASON_POLICY_MISSING_EXIT_PLAN,
    REASON_POLICY_MISSING_MAX_LOSS,
    REASON_POLICY_NOT_PAPER_ONLY,
    REASON_POLICY_UNDEFINED_RISK,
    validate_pre_trade_invariants,
)

DEFAULT_EXIT_PLAN = ExitPlan(profit_target_pct=0.50, stop_loss_multiple=2.0)


def test_policy_audit_rejects_non_paper_mode():
    decision = validate_pre_trade_invariants(
        _candidate(),
        settings=load_settings(env={}),
        mode="live",
    )

    assert decision.approved is False
    assert REASON_POLICY_NOT_PAPER_ONLY in decision.reason_codes


def test_policy_audit_rejects_0dte_even_for_high_score_candidate():
    decision = validate_pre_trade_invariants(
        _candidate(dte=0, entry_score=99),
        settings=load_settings(env={}),
        mode="paper",
    )

    assert decision.approved is False
    assert REASON_POLICY_0DTE_FORBIDDEN in decision.reason_codes


def test_policy_audit_rejects_market_order():
    decision = validate_pre_trade_invariants(
        _candidate(order_type=OrderType.MARKET),
        settings=load_settings(env={}),
        mode="paper",
    )

    assert decision.approved is False
    assert REASON_POLICY_MARKET_ORDER_FORBIDDEN in decision.reason_codes


def test_policy_audit_rejects_missing_max_loss_and_exit_plan():
    decision = validate_pre_trade_invariants(
        _candidate(max_loss=None, exit_plan=None),
        settings=load_settings(env={}),
        mode="paper",
    )

    assert decision.approved is False
    assert REASON_POLICY_MISSING_MAX_LOSS in decision.reason_codes
    assert REASON_POLICY_MISSING_EXIT_PLAN in decision.reason_codes


def test_policy_audit_rejects_undefined_risk_short_option():
    decision = validate_pre_trade_invariants(
        replace(_candidate(), legs=(_leg(450, OptionAction.SELL),)),
        settings=load_settings(env={}),
        mode="paper",
    )

    assert decision.approved is False
    assert REASON_POLICY_UNDEFINED_RISK in decision.reason_codes


def _candidate(
    *,
    dte: int = 30,
    entry_score: float = 75,
    max_loss: float | None = 50,
    exit_plan: ExitPlan | None = DEFAULT_EXIT_PLAN,
    order_type: OrderType = OrderType.LIMIT,
) -> StrategyCandidate:
    return StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="QQQ",
        legs=(
            _leg(450, OptionAction.SELL),
            _leg(449, OptionAction.BUY),
        ),
        dte=dte,
        entry_score=entry_score,
        max_profit=50,
        max_loss=max_loss,
        expected_credit_or_debit=50,
        reason_codes=("fixture", "experiment", "experiment_hypothesis:test"),
        exit_plan=exit_plan,
        order_type=order_type,
    )


def _leg(strike: float, action: OptionAction) -> OptionLeg:
    expiration = date(2026, 6, 19)
    return OptionLeg(
        contract=OptionContract(
            symbol=f"QQQ {expiration.isoformat()} {strike} put",
            underlying="QQQ",
            expiration=expiration,
            strike=strike,
            option_type=OptionType.PUT,
            bid=1.00,
            ask=1.05,
            mid=1.025,
            delta=-0.20,
            volume=100,
            open_interest=1000,
        ),
        action=action,
    )
