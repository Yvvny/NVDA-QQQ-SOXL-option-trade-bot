from __future__ import annotations

from dataclasses import dataclass

from trading_bot.broker.base import BrokerAdapter, BrokerResult
from trading_bot.broker.mock_broker import MockBroker
from trading_bot.core.models import RiskDecision, StrategyCandidate
from trading_bot.execution.order_builder import OptionOrder, OrderBuilder
from trading_bot.risk.engine import RiskEngine
from trading_bot.risk.portfolio import PortfolioState
from trading_bot.risk.sizing import PositionSizer
from trading_bot.storage.audit import JsonlAuditLogger


@dataclass(frozen=True)
class DryRunExecutionResult:
    status: str
    risk_decision: RiskDecision
    order: OptionOrder | None
    broker_result: BrokerResult | None


class DryRunExecutor:
    def __init__(
        self,
        risk_engine: RiskEngine | None = None,
        order_builder: OrderBuilder | None = None,
        broker: BrokerAdapter | None = None,
        audit_logger: JsonlAuditLogger | None = None,
        position_sizer: PositionSizer | None = None,
    ) -> None:
        self.risk_engine = risk_engine or RiskEngine()
        self.order_builder = order_builder or OrderBuilder()
        self.broker = broker or MockBroker()
        self.audit_logger = audit_logger or JsonlAuditLogger()
        self.position_sizer = position_sizer or PositionSizer()

    def execute(
        self,
        candidate: StrategyCandidate,
        portfolio_state: PortfolioState,
    ) -> DryRunExecutionResult:
        candidate = self.position_sizer.size_candidate(candidate, portfolio_state)
        risk_decision = self.risk_engine.evaluate(candidate, portfolio_state)
        if not risk_decision.approved:
            result = DryRunExecutionResult(
                status="rejected",
                risk_decision=risk_decision,
                order=None,
                broker_result=None,
            )
            self._record(candidate, result)
            return result

        order = self.order_builder.build(candidate)
        broker_result = self.broker.dry_run(order)
        result = DryRunExecutionResult(
            status="dry_run_accepted" if broker_result.accepted else "dry_run_rejected",
            risk_decision=risk_decision,
            order=order,
            broker_result=broker_result,
        )
        self._record(candidate, result)
        return result

    def _record(self, candidate: StrategyCandidate, result: DryRunExecutionResult) -> None:
        self.audit_logger.record(
            {
                "event_type": "candidate_dry_run",
                "status": result.status,
                "candidate": candidate,
                "risk_decision": result.risk_decision,
                "order": result.order,
                "broker_result": result.broker_result,
            }
        )
