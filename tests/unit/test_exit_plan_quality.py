from __future__ import annotations

from dataclasses import replace
from datetime import date

from trading_bot.config.settings import load_settings
from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.risk.exit_plan_quality import (
    EXIT_PLAN_MISSING,
    EXIT_PLAN_PLANNED_LOSS_PCT_TOO_HIGH,
    EXIT_PLAN_PLANNED_RR_BELOW_MIN,
    exit_plan_quality,
    exit_plan_quality_sort_key,
)

EXPIRATION = date(2026, 6, 19)


def test_credit_spread_exit_plan_quality_reports_planned_geometry():
    settings = load_settings(env={})
    candidate = _credit_spread(exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.5))

    result = exit_plan_quality(candidate, settings)

    assert result.approved is True
    assert result.planned_profit == 10.0
    assert result.planned_loss == 50.0
    assert result.planned_reward_risk == 0.2
    assert result.planned_loss_pct_of_max_loss == 0.625
    assert EXIT_PLAN_PLANNED_RR_BELOW_MIN in result.warning_reasons
    assert EXIT_PLAN_PLANNED_LOSS_PCT_TOO_HIGH in result.warning_reasons


def test_missing_exit_plan_is_blocking_even_in_monitor_only_mode():
    settings = load_settings(env={})
    candidate = _credit_spread(exit_plan=None)

    result = exit_plan_quality(candidate, settings)

    assert result.approved is False
    assert result.blocking_reasons == (EXIT_PLAN_MISSING,)


def test_low_planned_reward_risk_becomes_blocking_when_monitor_only_disabled():
    settings = load_settings(env={})
    settings = replace(
        settings,
        strategy=replace(settings.strategy, exit_plan_quality_monitor_only=False),
    )
    candidate = _credit_spread(exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.5))

    result = exit_plan_quality(candidate, settings)

    assert result.approved is False
    assert EXIT_PLAN_PLANNED_RR_BELOW_MIN in result.blocking_reasons
    assert EXIT_PLAN_PLANNED_LOSS_PCT_TOO_HIGH in result.blocking_reasons


def test_debit_spread_exit_plan_quality_caps_profit_target_by_debit_return():
    settings = load_settings(env={})
    candidate = _debit_spread(exit_plan=ExitPlan(profit_target_pct=0.75, stop_loss_pct=0.45))

    result = exit_plan_quality(candidate, settings)

    assert result.approved is True
    assert result.planned_profit == 30.0
    assert result.planned_loss == 22.5
    assert result.planned_reward_risk == 1.3333
    assert result.planned_loss_pct_of_max_loss == 0.45


def test_exit_plan_quality_sort_key_prefers_better_planned_geometry():
    settings = load_settings(env={})
    worse = _credit_spread(
        exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.5),
    )
    better = _credit_spread(
        exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=1.5),
    )

    assert exit_plan_quality_sort_key(better, settings) > exit_plan_quality_sort_key(
        worse,
        settings,
    )


def _credit_spread(exit_plan: ExitPlan | None) -> StrategyCandidate:
    return StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="QQQ",
        legs=(
            OptionLeg(_contract("put", 450, 0.45), OptionAction.SELL),
            OptionLeg(_contract("put", 449, 0.25), OptionAction.BUY),
        ),
        dte=30,
        entry_score=75,
        max_profit=20.0,
        max_loss=80.0,
        expected_credit_or_debit=20.0,
        reason_codes=(),
        exit_plan=exit_plan,
    )


def _debit_spread(exit_plan: ExitPlan | None) -> StrategyCandidate:
    return StrategyCandidate(
        strategy_name="call_debit_spread",
        underlying="QQQ",
        legs=(
            OptionLeg(_contract("call", 450, 0.80), OptionAction.BUY),
            OptionLeg(_contract("call", 452, 0.30), OptionAction.SELL),
        ),
        dte=21,
        entry_score=80,
        max_profit=150.0,
        max_loss=50.0,
        expected_credit_or_debit=50.0,
        reason_codes=(),
        exit_plan=exit_plan,
    )


def _contract(option_type: str, strike: float, mid: float) -> OptionContract:
    return OptionContract(
        symbol=f"QQQ {EXPIRATION.isoformat()} {strike} {option_type}",
        underlying="QQQ",
        expiration=EXPIRATION,
        strike=strike,
        option_type=OptionType(option_type),
        bid=mid - 0.01,
        ask=mid + 0.01,
        mid=mid,
        delta=0.2,
        volume=100,
        open_interest=1000,
    )
