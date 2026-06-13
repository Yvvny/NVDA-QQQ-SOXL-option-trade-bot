from dataclasses import replace
from datetime import date

from trading_bot.config.settings import load_settings
from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.risk.allocation import (
    REASON_ALLOCATION_CLUSTER_CAP_EXCEEDED,
    REASON_ALLOCATION_EXPERIMENTAL_BUDGET_EXCEEDED,
    REASON_ALLOCATION_MAX_ACTIVE_EXPERIMENTS_EXCEEDED,
    REASON_ALLOCATION_MISSING_EXPERIMENT_METADATA,
    REASON_ALLOCATION_SYMBOL_CAP_EXCEEDED,
    REASON_ALLOCATION_SYMBOL_REQUIRES_EXPERIMENT,
    AllocationPosition,
    validate_symbol_allocation,
)


def test_symbol_allocation_blocks_existing_nvda_over_cap():
    decision = validate_symbol_allocation(
        _candidate("NVDA", max_loss=10),
        account_equity=1705.5,
        open_positions=(AllocationPosition("NVDA", "call_debit_spread", 507.5),),
        settings=load_settings(env={}),
        preservation_mode_active=True,
    )

    assert decision.approved is False
    assert REASON_ALLOCATION_SYMBOL_CAP_EXCEEDED in decision.reason_codes


def test_symbol_allocation_blocks_correlated_tech_beta_cluster():
    decision = validate_symbol_allocation(
        _candidate("QQQ", max_loss=10),
        account_equity=1705.5,
        open_positions=(AllocationPosition("NVDA", "call_debit_spread", 507.5),),
        settings=load_settings(env={}),
        preservation_mode_active=True,
    )

    assert decision.approved is False
    assert REASON_ALLOCATION_CLUSTER_CAP_EXCEEDED in decision.reason_codes


def test_soxl_requires_experiment_tag():
    decision = validate_symbol_allocation(
        _candidate("SOXL", max_loss=10),
        account_equity=2000,
        open_positions=(),
        settings=load_settings(env={}),
        preservation_mode_active=True,
    )

    assert decision.approved is False
    assert REASON_ALLOCATION_SYMBOL_REQUIRES_EXPERIMENT in decision.reason_codes


def test_experiment_trade_must_fit_budget_and_metadata():
    decision = validate_symbol_allocation(
        _candidate("SOXL", max_loss=70, reason_codes=("experiment",)),
        account_equity=2000,
        open_positions=(),
        settings=load_settings(env={}),
        preservation_mode_active=True,
    )

    assert decision.approved is False
    assert REASON_ALLOCATION_EXPERIMENTAL_BUDGET_EXCEEDED in decision.reason_codes
    assert REASON_ALLOCATION_MISSING_EXPERIMENT_METADATA in decision.reason_codes


def test_only_one_active_experiment_is_allowed():
    decision = validate_symbol_allocation(
        _candidate(
            "SOXL",
            max_loss=40,
            reason_codes=("experiment", "experiment_hypothesis:test_small_soxl"),
        ),
        account_equity=2000,
        open_positions=(
            AllocationPosition("QQQ", "put_credit_spread", 40, is_experiment=True),
        ),
        settings=load_settings(env={}),
        preservation_mode_active=True,
    )

    assert decision.approved is False
    assert REASON_ALLOCATION_MAX_ACTIVE_EXPERIMENTS_EXCEEDED in decision.reason_codes


def test_allocation_gate_is_bypassed_outside_preservation_mode_by_default():
    settings = load_settings(env={})
    settings = replace(settings, allocation=replace(settings.allocation, preservation_only=True))

    decision = validate_symbol_allocation(
        _candidate("NVDA", max_loss=500),
        account_equity=2000,
        open_positions=(AllocationPosition("NVDA", "call_debit_spread", 500),),
        settings=settings,
        preservation_mode_active=False,
    )

    assert decision.approved is True


def _candidate(
    underlying: str,
    *,
    max_loss: float,
    reason_codes: tuple[str, ...] = ("fixture",),
) -> StrategyCandidate:
    expiration = date(2026, 6, 19)
    option_type = OptionType.CALL if underlying in {"NVDA", "SOXL"} else OptionType.PUT
    strategy_name = (
        "call_debit_spread" if option_type == OptionType.CALL else "put_credit_spread"
    )
    return StrategyCandidate(
        strategy_name=strategy_name,
        underlying=underlying,
        legs=(
            OptionLeg(
                contract=OptionContract(
                    symbol=f"{underlying} {expiration.isoformat()} 100 {option_type.value}",
                    underlying=underlying,
                    expiration=expiration,
                    strike=100,
                    option_type=option_type,
                    bid=1.00,
                    ask=1.10,
                    mid=1.05,
                    delta=0.50,
                    volume=100,
                    open_interest=1000,
                ),
                action=OptionAction.BUY,
            ),
            OptionLeg(
                contract=OptionContract(
                    symbol=f"{underlying} {expiration.isoformat()} 105 {option_type.value}",
                    underlying=underlying,
                    expiration=expiration,
                    strike=105,
                    option_type=option_type,
                    bid=0.40,
                    ask=0.50,
                    mid=0.45,
                    delta=0.30,
                    volume=100,
                    open_interest=1000,
                ),
                action=OptionAction.SELL,
            ),
        ),
        dte=30,
        entry_score=75,
        max_profit=100,
        max_loss=max_loss,
        expected_credit_or_debit=max_loss,
        reason_codes=reason_codes,
        exit_plan=ExitPlan(profit_target_pct=0.50, stop_loss_pct=0.45),
    )
