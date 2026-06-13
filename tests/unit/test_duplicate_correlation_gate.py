from datetime import date, datetime

from trading_bot.config.settings import load_settings
from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.risk.duplicate_correlation_gate import (
    REASON_DUPLICATE_EXACT_POSITION,
    REASON_DUPLICATE_NEAR_POSITION,
    REASON_MAX_CORRELATED_TECH_BETA_EXPOSURE,
    REASON_MAX_SYMBOL_DIRECTION_EXPOSURE,
    REASON_MAX_THESIS_BUCKET_EXPOSURE,
    REASON_RECENT_STOPOUT_COOLDOWN,
    GateLeg,
    GatePosition,
    evaluate_duplicate_correlation_gate,
)


def test_duplicate_gate_rejects_exact_duplicate_nvda_call_debit_spread():
    candidate = _candidate("NVDA", "call_debit_spread")

    decision = evaluate_duplicate_correlation_gate(
        candidate,
        open_positions=(_position("NVDA", "call_debit_spread"),),
        closed_positions=(),
        account_equity=2000,
        settings=load_settings(env={}),
        preservation_mode_active=True,
    )

    assert decision.approved is False
    assert REASON_DUPLICATE_EXACT_POSITION in decision.reason_codes


def test_duplicate_gate_rejects_near_duplicate_same_zone():
    decision = evaluate_duplicate_correlation_gate(
        _candidate("NVDA", "call_debit_spread", long_strike=101, short_strike=106),
        open_positions=(_position("NVDA", "call_debit_spread"),),
        closed_positions=(),
        account_equity=2000,
        settings=load_settings(env={}),
        preservation_mode_active=True,
    )

    assert decision.approved is False
    assert REASON_DUPLICATE_NEAR_POSITION in decision.reason_codes


def test_duplicate_gate_rejects_correlated_bullish_tech_beta_exposure():
    decision = evaluate_duplicate_correlation_gate(
        _candidate("SOXL", "call_debit_spread"),
        open_positions=(_position("NVDA", "call_debit_spread"),),
        closed_positions=(),
        account_equity=2000,
        settings=load_settings(env={}),
        preservation_mode_active=True,
    )

    assert decision.approved is False
    assert REASON_MAX_CORRELATED_TECH_BETA_EXPOSURE in decision.reason_codes
    assert REASON_MAX_THESIS_BUCKET_EXPOSURE in decision.reason_codes


def test_duplicate_gate_rejects_same_symbol_direction_cap():
    decision = evaluate_duplicate_correlation_gate(
        _candidate("NVDA", "put_credit_spread"),
        open_positions=(_position("NVDA", "call_debit_spread"),),
        closed_positions=(),
        account_equity=2000,
        settings=load_settings(env={}),
        preservation_mode_active=True,
    )

    assert decision.approved is False
    assert REASON_MAX_SYMBOL_DIRECTION_EXPOSURE in decision.reason_codes


def test_duplicate_gate_rejects_recent_same_symbol_stopout():
    decision = evaluate_duplicate_correlation_gate(
        _candidate("QQQ", "put_credit_spread"),
        open_positions=(),
        closed_positions=(
            _position(
                "QQQ",
                "put_credit_spread",
                closed_at="2026-06-07T10:00:00-04:00",
                exit_reason="stop_loss_multiple",
            ),
        ),
        account_equity=2000,
        settings=load_settings(env={}),
        preservation_mode_active=True,
        checked_at=datetime.fromisoformat("2026-06-07T12:00:00-04:00"),
    )

    assert decision.approved is False
    assert REASON_RECENT_STOPOUT_COOLDOWN in decision.reason_codes


def test_duplicate_gate_allows_unrelated_defined_risk_candidate():
    decision = evaluate_duplicate_correlation_gate(
        _candidate("IWM", "put_credit_spread"),
        open_positions=(_position("NVDA", "call_debit_spread"),),
        closed_positions=(),
        account_equity=2000,
        settings=load_settings(env={}),
        preservation_mode_active=True,
    )

    assert decision.approved is True


def _candidate(
    symbol: str,
    strategy_name: str,
    *,
    long_strike: float = 100,
    short_strike: float = 105,
) -> StrategyCandidate:
    expiration = date(2026, 6, 19)
    return StrategyCandidate(
        strategy_name=strategy_name,
        underlying=symbol,
        legs=(
            _leg(symbol, expiration, long_strike, OptionAction.BUY),
            _leg(symbol, expiration, short_strike, OptionAction.SELL),
        ),
        dte=30,
        entry_score=80,
        max_profit=100,
        max_loss=50,
        expected_credit_or_debit=50,
        reason_codes=("fixture",),
        exit_plan=ExitPlan(profit_target_pct=0.50, stop_loss_pct=0.45),
    )


def _position(
    symbol: str,
    strategy_name: str,
    *,
    long_strike: float = 100,
    short_strike: float = 105,
    closed_at: str | None = None,
    exit_reason: str | None = None,
) -> GatePosition:
    expiration = "2026-06-19"
    return GatePosition(
        symbol=symbol,
        strategy_name=strategy_name,
        max_loss=50,
        legs=(
            GateLeg("buy", "call", long_strike, expiration),
            GateLeg("sell", "call", short_strike, expiration),
        ),
        closed_at=closed_at,
        exit_reason=exit_reason,
    )


def _leg(
    symbol: str,
    expiration: date,
    strike: float,
    action: OptionAction,
) -> OptionLeg:
    return OptionLeg(
        contract=OptionContract(
            symbol=f"{symbol} {expiration.isoformat()} {strike} call",
            underlying=symbol,
            expiration=expiration,
            strike=strike,
            option_type=OptionType.CALL,
            bid=1.00,
            ask=1.05,
            mid=1.025,
            delta=0.50,
            volume=100,
            open_interest=1000,
        ),
        action=action,
    )
