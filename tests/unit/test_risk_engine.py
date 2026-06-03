from datetime import date

from trading_bot.core.enums import OptionAction, OptionType, OrderType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.risk.engine import (
    REASON_0DTE_FORBIDDEN,
    REASON_APPROVED,
    REASON_CONSECUTIVE_LOSS_LIMIT_EXCEEDED,
    REASON_DAILY_LOSS_LIMIT_EXCEEDED,
    REASON_EVENT_RISK_BLOCK,
    REASON_KILL_SWITCH_ACTIVE,
    REASON_LIQUIDITY_BLOCK,
    REASON_MARKET_ORDER_OPTIONS_FORBIDDEN,
    REASON_MAX_NEW_TRADES_TODAY_EXCEEDED,
    REASON_MAX_NEW_TRADES_WEEK_EXCEEDED,
    REASON_MISSING_EXIT_PLAN,
    REASON_MISSING_MAX_LOSS,
    REASON_NAKED_SHORT_OPTION_FORBIDDEN,
    REASON_PER_TRADE_MAX_LOSS_EXCEEDED,
    REASON_SOXL_MAX_LOSS_EXCEEDED,
    REASON_STRATEGY_CONCENTRATION_EXCEEDED,
    REASON_SYMBOL_CONCENTRATION_EXCEEDED,
    REASON_TOTAL_OPEN_MAX_LOSS_EXCEEDED,
    REASON_UNDEFINED_RISK_FORBIDDEN,
    REASON_WEEKLY_LOSS_LIMIT_EXCEEDED,
    RiskEngine,
)
from trading_bot.risk.kill_switch import KillSwitchState
from trading_bot.risk.portfolio import OpenPosition, PortfolioState

EXPIRATION = date(2026, 6, 19)


def test_approves_defined_risk_limit_order_candidate():
    decision = RiskEngine().evaluate(_candidate(), _portfolio())

    assert decision.approved is True
    assert decision.reason_codes == (REASON_APPROVED,)
    assert decision.max_loss == 100
    assert decision.adjusted_size == 1


def test_rejects_naked_short_options_and_undefined_risk():
    decision = RiskEngine().evaluate(
        _candidate(legs=(_leg(OptionAction.SELL, 450, "put"),)), _portfolio()
    )

    assert decision.approved is False
    assert REASON_NAKED_SHORT_OPTION_FORBIDDEN in decision.reason_codes
    assert REASON_UNDEFINED_RISK_FORBIDDEN in decision.reason_codes


def test_rejects_0dte_options():
    decision = RiskEngine().evaluate(_candidate(dte=0), _portfolio())

    assert decision.approved is False
    assert REASON_0DTE_FORBIDDEN in decision.reason_codes


def test_rejects_missing_max_loss():
    decision = RiskEngine().evaluate(_candidate(max_loss=None), _portfolio())

    assert decision.approved is False
    assert REASON_MISSING_MAX_LOSS in decision.reason_codes


def test_rejects_market_orders_for_options():
    decision = RiskEngine().evaluate(_candidate(order_type=OrderType.MARKET), _portfolio())

    assert decision.approved is False
    assert REASON_MARKET_ORDER_OPTIONS_FORBIDDEN in decision.reason_codes


def test_rejects_missing_exit_plan():
    decision = RiskEngine().evaluate(_candidate(exit_plan=None), _portfolio())

    assert decision.approved is False
    assert REASON_MISSING_EXIT_PLAN in decision.reason_codes


def test_rejects_excessive_per_trade_risk():
    decision = RiskEngine().evaluate(_candidate(max_loss=450, entry_score=70), _portfolio())

    assert decision.approved is False
    assert REASON_PER_TRADE_MAX_LOSS_EXCEEDED in decision.reason_codes


def test_rejects_trade_when_available_cash_budget_is_smaller_than_equity_budget():
    portfolio = _portfolio(open_positions=(OpenPosition("QQQ", "put_credit_spread", 300),))

    decision = RiskEngine().evaluate(_candidate(max_loss=350, entry_score=70), portfolio)

    assert decision.approved is False
    assert REASON_PER_TRADE_MAX_LOSS_EXCEEDED in decision.reason_codes


def test_high_score_can_use_high_score_per_trade_limit():
    decision = RiskEngine().evaluate(_candidate(max_loss=450, entry_score=85), _portfolio())

    assert decision.approved is True
    assert decision.reason_codes == (REASON_APPROVED,)


def test_rejects_soxl_trade_above_soxl_cap():
    decision = RiskEngine().evaluate(
        _candidate(underlying="SOXL", max_loss=175, entry_score=85),
        _portfolio(),
    )

    assert decision.approved is False
    assert REASON_SOXL_MAX_LOSS_EXCEEDED in decision.reason_codes


def test_rejects_excessive_total_open_risk():
    portfolio = _portfolio(open_positions=(OpenPosition("QQQ", "put_credit_spread", 950),))

    decision = RiskEngine().evaluate(_candidate(max_loss=100), portfolio)

    assert decision.approved is False
    assert REASON_TOTAL_OPEN_MAX_LOSS_EXCEEDED in decision.reason_codes


def test_rejects_kill_switch_state():
    portfolio = _portfolio(kill_switch=KillSwitchState.triggered("daily_loss_limit_exceeded"))

    decision = RiskEngine().evaluate(_candidate(), portfolio)

    assert decision.approved is False
    assert REASON_KILL_SWITCH_ACTIVE in decision.reason_codes
    assert REASON_DAILY_LOSS_LIMIT_EXCEEDED in decision.reason_codes


def test_rejects_after_daily_loss_limit():
    decision = RiskEngine().evaluate(_candidate(), _portfolio(daily_realized_pnl=-200))

    assert decision.approved is False
    assert REASON_DAILY_LOSS_LIMIT_EXCEEDED in decision.reason_codes


def test_rejects_after_weekly_loss_limit():
    decision = RiskEngine().evaluate(_candidate(), _portfolio(weekly_realized_pnl=-400))

    assert decision.approved is False
    assert REASON_WEEKLY_LOSS_LIMIT_EXCEEDED in decision.reason_codes


def test_rejects_after_max_consecutive_losses():
    decision = RiskEngine().evaluate(_candidate(), _portfolio(consecutive_losses=3))

    assert decision.approved is False
    assert REASON_CONSECUTIVE_LOSS_LIMIT_EXCEEDED in decision.reason_codes


def test_rejects_after_max_new_trades_today():
    decision = RiskEngine().evaluate(_candidate(), _portfolio(new_trades_today=2))

    assert decision.approved is False
    assert REASON_MAX_NEW_TRADES_TODAY_EXCEEDED in decision.reason_codes


def test_rejects_after_max_new_trades_this_week():
    decision = RiskEngine().evaluate(_candidate(), _portfolio(new_trades_this_week=5))

    assert decision.approved is False
    assert REASON_MAX_NEW_TRADES_WEEK_EXCEEDED in decision.reason_codes


def test_rejects_same_symbol_concentration():
    portfolio = _portfolio(
        open_positions=(
            OpenPosition("QQQ", "iron_condor", 20),
            OpenPosition("QQQ", "call_debit_spread", 20),
        )
    )

    decision = RiskEngine().evaluate(_candidate(), portfolio)

    assert decision.approved is False
    assert REASON_SYMBOL_CONCENTRATION_EXCEEDED in decision.reason_codes


def test_rejects_same_strategy_concentration():
    portfolio = _portfolio(
        open_positions=(
            OpenPosition("SPY", "put_credit_spread", 20),
            OpenPosition("IWM", "put_credit_spread", 20),
            OpenPosition("SMH", "put_credit_spread", 20),
        )
    )

    decision = RiskEngine().evaluate(_candidate(), portfolio)

    assert decision.approved is False
    assert REASON_STRATEGY_CONCENTRATION_EXCEEDED in decision.reason_codes


def test_rejects_event_risk_block():
    decision = RiskEngine().evaluate(_candidate(event_risk_blocked=True), _portfolio())

    assert decision.approved is False
    assert REASON_EVENT_RISK_BLOCK in decision.reason_codes


def test_rejects_liquidity_block():
    decision = RiskEngine().evaluate(
        _candidate(liquidity_ok=False, liquidity_warnings=("wide_bid_ask",)),
        _portfolio(),
    )

    assert decision.approved is False
    assert REASON_LIQUIDITY_BLOCK in decision.reason_codes
    assert "wide_bid_ask" in decision.reason_codes


def _portfolio(**overrides):
    values = {"account_equity": 2000.0}
    values.update(overrides)
    return PortfolioState(**values)


def _candidate(**overrides):
    values = {
        "strategy_name": "put_credit_spread",
        "underlying": "QQQ",
        "legs": (
            _leg(OptionAction.SELL, 450, "put"),
            _leg(OptionAction.BUY, 445, "put"),
        ),
        "dte": 30,
        "entry_score": 75,
        "max_profit": 50,
        "max_loss": 100,
        "expected_credit_or_debit": 50,
        "reason_codes": ("fixture",),
        "exit_plan": ExitPlan(
            profit_target_pct=0.50,
            stop_loss_multiple=2.5,
            time_exit_dte=21,
        ),
    }
    values.update(overrides)
    return StrategyCandidate(**values)


def _leg(action: OptionAction, strike: float, option_type: str) -> OptionLeg:
    return OptionLeg(
        contract=OptionContract(
            symbol=f"QQQ {EXPIRATION.isoformat()} {strike} {option_type}",
            underlying="QQQ",
            expiration=EXPIRATION,
            strike=strike,
            option_type=OptionType(option_type),
            bid=1.00,
            ask=1.10,
            mid=1.05,
            delta=-0.25 if option_type == "put" else 0.25,
            volume=100,
            open_interest=1000,
        ),
        action=action,
    )
