from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_bot.core.models import RiskDecision, StrategyCandidate

SCHEMA_VERSION = "rl_dataset_v1"
DEFAULT_PAPER_RL_DATASET_PATH = Path("data/paper_rl_events.jsonl")


class PaperRLDatasetLogger:
    def __init__(self, path: str | Path = DEFAULT_PAPER_RL_DATASET_PATH) -> None:
        self.path = Path(path)

    def record_from_paper_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type") or "")
        if event_type == "paper_candidate_rejected":
            self.record_candidate_event("candidate_rejected", event)
        elif event_type == "paper_position_opened":
            self.record_candidate_event("paper_trade_approved", event)
        elif event_type == "paper_position_closed":
            self.record_outcome_event(event)

    def record_candidate_event(self, event_type: str, source_event: dict[str, Any]) -> None:
        candidate = source_event.get("candidate")
        if not isinstance(candidate, StrategyCandidate):
            return
        decision = source_event.get("risk_decision")
        record = _base_record(event_type)
        record.update(
            {
                "strategy_type": candidate.strategy_name,
                "symbol": candidate.underlying,
                "candidate_id": _candidate_id(candidate),
                "trade_id": _position_id(source_event.get("paper_position")),
                "features": _candidate_features(candidate),
                "risk_state": {},
                "decision": _decision_payload(decision),
                "outcome": {},
                "rejection_reasons": _decision_reasons(decision),
            }
        )
        self._append(record)

    def record_outcome_event(self, source_event: dict[str, Any]) -> None:
        closed_trade = source_event.get("paper_closed_trade")
        position = getattr(closed_trade, "position", None)
        if position is None:
            return
        record = _base_record("paper_trade_labeled")
        record.update(
            {
                "strategy_type": position.strategy_name,
                "symbol": position.underlying,
                "candidate_id": str(position.candidate_payload.get("candidate_id") or ""),
                "trade_id": position.position_id,
                "features": dict(position.candidate_payload),
                "risk_state": {
                    "max_loss": position.max_loss,
                    "max_profit": position.max_profit,
                },
                "decision": {"approved": True, "paper_entry_recorded": True},
                "outcome": {
                    "exit_reason": getattr(closed_trade, "exit_reason", None),
                    "realized_pnl": getattr(closed_trade, "realized_pnl", None),
                    "pnl_pct_of_max_loss": (
                        None
                        if position.max_loss <= 0
                        else round(
                            getattr(closed_trade, "realized_pnl", 0.0) / position.max_loss,
                            4,
                        )
                    ),
                    "label": _outcome_label(closed_trade),
                },
                "rejection_reasons": [],
            }
        )
        self._append(record)

    def _append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(record), sort_keys=True) + "\n")


def _base_record(event_type: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "paper_only": True,
        "shadow_mode": True,
        "rl_shadow_score": None,
        "strategy_type": "",
        "symbol": "",
        "candidate_id": "",
        "trade_id": None,
        "features": {},
        "risk_state": {},
        "decision": {},
        "outcome": {},
        "rejection_reasons": [],
    }


def _candidate_features(candidate: StrategyCandidate) -> dict[str, Any]:
    return {
        "dte": candidate.dte,
        "entry_score": candidate.entry_score,
        "max_loss": candidate.total_max_loss(),
        "max_profit": candidate.total_max_profit(),
        "expected_credit_or_debit": candidate.total_expected_credit_or_debit(),
        "quantity": candidate.quantity,
        "has_exit_plan": candidate.exit_plan is not None and candidate.exit_plan.is_defined(),
        "has_max_loss": candidate.total_max_loss() is not None,
        "legs": [
            {
                "action": leg.action.value,
                "symbol": leg.contract.symbol,
                "option_type": leg.contract.option_type.value,
                "strike": leg.contract.strike,
                "expiration": leg.contract.expiration.isoformat(),
                "bid": leg.contract.bid,
                "ask": leg.contract.ask,
                "mid": leg.contract.effective_mid(),
                "volume_missing": leg.contract.volume is None,
                "open_interest_missing": leg.contract.open_interest is None,
                "delta": leg.contract.delta,
                "iv": leg.contract.iv,
            }
            for leg in candidate.legs
        ],
        "reason_codes": list(candidate.reason_codes),
    }


def _decision_payload(decision: Any) -> dict[str, Any]:
    if not isinstance(decision, RiskDecision):
        return {}
    return {
        "approved": decision.approved,
        "reason_codes": list(decision.reason_codes),
        "max_loss": decision.max_loss,
        "adjusted_size": decision.adjusted_size,
    }


def _decision_reasons(decision: Any) -> list[str]:
    if not isinstance(decision, RiskDecision):
        return []
    return [str(reason) for reason in decision.reason_codes if str(reason) != "approved"]


def _position_id(value: Any) -> str | None:
    position_id = getattr(value, "position_id", None)
    return str(position_id) if position_id is not None else None


def _candidate_id(candidate: StrategyCandidate) -> str:
    payload = json.dumps(_candidate_features(candidate), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _outcome_label(closed_trade: Any) -> str:
    realized_pnl = float(getattr(closed_trade, "realized_pnl", 0.0) or 0.0)
    if realized_pnl > 0:
        return "good"
    if realized_pnl < 0:
        return "bad"
    return "neutral"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
