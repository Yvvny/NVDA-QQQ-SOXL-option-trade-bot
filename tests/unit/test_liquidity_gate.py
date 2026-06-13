from dataclasses import replace
from datetime import date

from trading_bot.config.settings import load_settings
from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.risk.liquidity import (
    REASON_LIQUIDITY_DATA_MISSING_OBSERVATION_ONLY,
    REASON_LIQUIDITY_INVALID_BID_ASK,
    REASON_LIQUIDITY_LOW_OI,
    REASON_LIQUIDITY_LOW_VOLUME,
    REASON_LIQUIDITY_MISSING_OI,
    REASON_LIQUIDITY_MISSING_VOLUME,
    REASON_LIQUIDITY_WIDE_LEG_MARKET,
    REASON_LIQUIDITY_WIDE_PACKAGE_MARKET,
    validate_candidate_liquidity,
)


def test_liquidity_gate_rejects_missing_volume_and_open_interest():
    decision = validate_candidate_liquidity(
        _candidate(volume=None, open_interest=None),
        load_settings(env={}),
    )

    assert decision.approved is False
    assert REASON_LIQUIDITY_MISSING_VOLUME in decision.reason_codes
    assert REASON_LIQUIDITY_MISSING_OI in decision.reason_codes


def test_liquidity_gate_rejects_zero_activity():
    decision = validate_candidate_liquidity(
        _candidate(volume=0, open_interest=0),
        load_settings(env={}),
    )

    assert decision.approved is False
    assert REASON_LIQUIDITY_LOW_VOLUME in decision.reason_codes
    assert REASON_LIQUIDITY_LOW_OI in decision.reason_codes


def test_liquidity_gate_rejects_invalid_bid_ask():
    decision = validate_candidate_liquidity(
        _candidate(short_bid=0, short_ask=0),
        load_settings(env={}),
    )

    assert decision.approved is False
    assert REASON_LIQUIDITY_INVALID_BID_ASK in decision.reason_codes


def test_liquidity_gate_rejects_wide_leg_market():
    decision = validate_candidate_liquidity(
        _candidate(short_bid=1.00, short_ask=1.30),
        load_settings(env={}),
    )

    assert decision.approved is False
    assert REASON_LIQUIDITY_WIDE_LEG_MARKET in decision.reason_codes


def test_liquidity_gate_rejects_wide_package_market():
    settings = load_settings(env={})
    settings = replace(
        settings,
        liquidity=replace(settings.liquidity, max_abs_bid_ask_width=1.00),
    )

    decision = validate_candidate_liquidity(
        _candidate(short_bid=1.00, short_ask=1.08, long_bid=0.48, long_ask=0.56),
        settings,
    )

    assert decision.approved is False
    assert REASON_LIQUIDITY_WIDE_PACKAGE_MARKET in decision.reason_codes


def test_liquidity_gate_approves_tight_liquid_spread():
    decision = validate_candidate_liquidity(
        _candidate(short_bid=1.00, short_ask=1.03, long_bid=0.45, long_ask=0.48),
        load_settings(env={}),
    )

    assert decision.approved is True


def test_observation_mode_allows_missing_activity_for_one_contract_only():
    settings = load_settings(env={})
    settings = replace(
        settings,
        liquidity=replace(settings.liquidity, paper_liquidity_observation_mode=True),
    )

    decision = validate_candidate_liquidity(
        _candidate(volume=None, open_interest=None),
        settings,
    )

    assert decision.approved is True
    assert REASON_LIQUIDITY_DATA_MISSING_OBSERVATION_ONLY in decision.reason_codes


def _candidate(
    *,
    short_bid: float | None = 1.00,
    short_ask: float | None = 1.03,
    long_bid: float | None = 0.45,
    long_ask: float | None = 0.48,
    volume: int | None = 100,
    open_interest: int | None = 1000,
    quantity: int = 1,
) -> StrategyCandidate:
    expiration = date(2026, 6, 19)
    return StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="QQQ",
        legs=(
            OptionLeg(
                contract=OptionContract(
                    symbol=f"QQQ {expiration.isoformat()} 450 put",
                    underlying="QQQ",
                    expiration=expiration,
                    strike=450,
                    option_type=OptionType.PUT,
                    bid=short_bid,
                    ask=short_ask,
                    mid=_mid(short_bid, short_ask),
                    delta=-0.20,
                    volume=volume,
                    open_interest=open_interest,
                ),
                action=OptionAction.SELL,
            ),
            OptionLeg(
                contract=OptionContract(
                    symbol=f"QQQ {expiration.isoformat()} 449 put",
                    underlying="QQQ",
                    expiration=expiration,
                    strike=449,
                    option_type=OptionType.PUT,
                    bid=long_bid,
                    ask=long_ask,
                    mid=_mid(long_bid, long_ask),
                    delta=-0.10,
                    volume=volume,
                    open_interest=open_interest,
                ),
                action=OptionAction.BUY,
            ),
        ),
        dte=30,
        entry_score=75,
        max_profit=50,
        max_loss=50,
        expected_credit_or_debit=50,
        reason_codes=("fixture",),
        exit_plan=ExitPlan(profit_target_pct=0.50, stop_loss_multiple=2.0),
        quantity=quantity,
    )


def _mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2
