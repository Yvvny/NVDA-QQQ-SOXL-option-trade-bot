from datetime import date

import pytest

from trading_bot.broker.base import LiveTradingDisabledError
from trading_bot.broker.mock_broker import MockBroker
from trading_bot.core.enums import OptionAction, OptionType, OrderType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.execution import DryRunExecutor, OrderBuilder
from trading_bot.risk.engine import REASON_MARKET_ORDER_OPTIONS_FORBIDDEN
from trading_bot.risk.portfolio import PortfolioState
from trading_bot.storage.audit import JsonlAuditLogger

EXPIRATION = date(2026, 6, 19)


def test_order_builder_uses_limit_price_offset_for_credit_spread():
    order = OrderBuilder().build(_candidate())

    assert order.order_type == OrderType.LIMIT
    assert order.price_effect == "credit"
    assert order.limit_price == 0.48
    assert order.max_loss == 100
    assert [leg.action.value for leg in order.legs] == ["sell", "buy"]


def test_dry_run_executor_approves_and_logs_candidate(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    result = DryRunExecutor(audit_logger=JsonlAuditLogger(audit_path)).execute(
        _candidate(),
        PortfolioState(account_equity=2000),
    )

    assert result.status == "dry_run_accepted"
    assert result.risk_decision.approved is True
    assert result.broker_result is not None
    assert audit_path.exists()
    assert '"status": "dry_run_accepted"' in audit_path.read_text(encoding="utf-8")


def test_dry_run_executor_logs_rejected_candidate(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    result = DryRunExecutor(audit_logger=JsonlAuditLogger(audit_path)).execute(
        _candidate(order_type=OrderType.MARKET),
        PortfolioState(account_equity=2000),
    )

    assert result.status == "rejected"
    assert result.order is None
    assert REASON_MARKET_ORDER_OPTIONS_FORBIDDEN in result.risk_decision.reason_codes
    assert '"status": "rejected"' in audit_path.read_text(encoding="utf-8")


def test_mock_broker_submit_is_disabled():
    order = OrderBuilder().build(_candidate())

    with pytest.raises(LiveTradingDisabledError):
        MockBroker().submit(order)


def _candidate(**overrides):
    values = {
        "strategy_name": "put_credit_spread",
        "underlying": "QQQ",
        "legs": (
            OptionLeg(contract=_contract("put", 450), action=OptionAction.SELL),
            OptionLeg(contract=_contract("put", 449), action=OptionAction.BUY),
        ),
        "dte": 30,
        "entry_score": 75,
        "max_profit": 50,
        "max_loss": 100,
        "expected_credit_or_debit": 50,
        "reason_codes": ("fixture",),
        "exit_plan": ExitPlan(profit_target_pct=0.50, stop_loss_multiple=2.5),
    }
    values.update(overrides)
    return StrategyCandidate(**values)


def _contract(option_type: str, strike: float) -> OptionContract:
    return OptionContract(
        symbol=f"QQQ {EXPIRATION.isoformat()} {strike} {option_type}",
        underlying="QQQ",
        expiration=EXPIRATION,
        strike=strike,
        option_type=OptionType(option_type),
        bid=0.20,
        ask=0.30,
        mid=0.25,
        delta=-0.25,
        volume=100,
        open_interest=1000,
    )
