import json
from datetime import date

from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import (
    ExitPlan,
    OptionContract,
    OptionLeg,
    RiskDecision,
    StrategyCandidate,
)
from trading_bot.paper import PaperClosedTrade, PaperPosition
from trading_bot.paper_rl_dataset_logger import SCHEMA_VERSION, PaperRLDatasetLogger


def test_rl_dataset_logger_records_rejected_candidate_schema(tmp_path):
    path = tmp_path / "rl.jsonl"
    logger = PaperRLDatasetLogger(path)

    logger.record_from_paper_event(
        {
            "event_type": "paper_candidate_rejected",
            "candidate": _candidate(),
            "risk_decision": RiskDecision(
                approved=False,
                reason_codes=("liquidity_missing_volume",),
                max_loss=50,
                adjusted_size=None,
            ),
        }
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["event_type"] == "candidate_rejected"
    assert record["paper_only"] is True
    assert record["shadow_mode"] is True
    assert record["rl_shadow_score"] is None
    assert record["rejection_reasons"] == ["liquidity_missing_volume"]
    assert record["features"]["has_max_loss"] is True
    assert record["features"]["has_exit_plan"] is True


def test_rl_dataset_logger_records_closed_trade_outcome_label(tmp_path):
    path = tmp_path / "rl.jsonl"
    logger = PaperRLDatasetLogger(path)

    logger.record_from_paper_event(
        {
            "event_type": "paper_position_closed",
            "paper_closed_trade": PaperClosedTrade(
                position=_position(),
                closed_at="2026-06-07T10:00:00-04:00",
                exit_reason="stop_loss_multiple",
                realized_pnl=-25,
            ),
        }
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["event_type"] == "paper_trade_labeled"
    assert record["trade_id"] == "paper-1"
    assert record["outcome"]["label"] == "bad"
    assert record["outcome"]["pnl_pct_of_max_loss"] == -0.5


def _candidate() -> StrategyCandidate:
    expiration = date(2026, 6, 19)
    return StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="QQQ",
        legs=(
            _leg(expiration, 450, OptionAction.SELL),
            _leg(expiration, 449, OptionAction.BUY),
        ),
        dte=30,
        entry_score=75,
        max_profit=50,
        max_loss=50,
        expected_credit_or_debit=50,
        reason_codes=("fixture",),
        exit_plan=ExitPlan(profit_target_pct=0.50, stop_loss_multiple=2.0),
    )


def _position() -> PaperPosition:
    return PaperPosition(
        position_id="paper-1",
        opened_at="2026-06-07T09:30:00-04:00",
        underlying="QQQ",
        strategy_name="put_credit_spread",
        dte_at_entry=30,
        entry_score=75,
        max_profit=50,
        max_loss=50,
        expected_credit_or_debit=50,
        price_effect="credit",
        entry_value=-50,
        legs=(),
        exit_plan={},
    )


def _leg(expiration: date, strike: float, action: OptionAction) -> OptionLeg:
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
