from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from trading_bot.backtest.fills import FillAssumption, estimate_fill_price
from trading_bot.backtest.metrics import BacktestMetrics, BacktestTrade, calculate_metrics
from trading_bot.core.models import StrategyCandidate
from trading_bot.execution.order_builder import OrderBuilder
from trading_bot.risk.engine import RiskEngine
from trading_bot.risk.portfolio import PortfolioState

CONTRACT_MULTIPLIER = 100


@dataclass(frozen=True)
class OptionPositionSnapshot:
    date: date
    dte: int
    mark_price: float
    underlying_price: float | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class BacktestScenario:
    trade_id: str
    candidate: StrategyCandidate
    entry_date: date
    exit_snapshots: tuple[OptionPositionSnapshot, ...]
    portfolio_state: PortfolioState | None = None
    fill_ratio: float = 1.0


@dataclass(frozen=True)
class BacktestSimulationConfig:
    fill_assumption: FillAssumption = field(default_factory=FillAssumption)
    commission_per_contract: float = 0.65
    min_fill_ratio: float = 1.0


@dataclass(frozen=True)
class BacktestSkippedTrade:
    trade_id: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class BacktestResult:
    metrics: BacktestMetrics
    trades: tuple[BacktestTrade, ...]
    skipped_trades: tuple[BacktestSkippedTrade, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)

    def write_json_report(self, path: str | Path) -> None:
        report_path = Path(path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_json = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        report_path.write_text(report_json, encoding="utf-8")


class BacktestEngine:
    def __init__(
        self,
        initial_equity: float = 2000.0,
        *,
        risk_engine: RiskEngine | None = None,
        order_builder: OrderBuilder | None = None,
        simulation_config: BacktestSimulationConfig | None = None,
    ) -> None:
        self.initial_equity = initial_equity
        self.risk_engine = risk_engine or RiskEngine()
        self.order_builder = order_builder or OrderBuilder()
        self.simulation_config = simulation_config or BacktestSimulationConfig()

    def run_from_trade_results(self, trades: list[BacktestTrade]) -> BacktestResult:
        return BacktestResult(
            metrics=calculate_metrics(trades, self.initial_equity),
            trades=tuple(trades),
        )

    def run_scenarios(self, scenarios: list[BacktestScenario]) -> BacktestResult:
        trades: list[BacktestTrade] = []
        skipped: list[BacktestSkippedTrade] = []

        for scenario in scenarios:
            if scenario.fill_ratio < self.simulation_config.min_fill_ratio:
                skipped.append(
                    BacktestSkippedTrade(scenario.trade_id, ("partial_fill_below_threshold",))
                )
                continue

            portfolio_state = scenario.portfolio_state or PortfolioState(
                account_equity=self.initial_equity
            )
            risk_decision = self.risk_engine.evaluate(scenario.candidate, portfolio_state)
            if not risk_decision.approved:
                skipped.append(BacktestSkippedTrade(scenario.trade_id, risk_decision.reason_codes))
                continue

            if not scenario.exit_snapshots:
                skipped.append(BacktestSkippedTrade(scenario.trade_id, ("missing_exit_snapshots",)))
                continue

            order = self.order_builder.build(scenario.candidate)
            exit_snapshot, exit_reason = _select_exit_snapshot(
                scenario.candidate,
                scenario.exit_snapshots,
            )
            entry_side = "sell" if order.price_effect == "credit" else "buy"
            exit_side = "buy" if order.price_effect == "credit" else "sell"
            entry_mid = scenario.candidate.expected_credit_or_debit / CONTRACT_MULTIPLIER
            entry_price = estimate_fill_price(
                entry_mid,
                entry_side,
                self.simulation_config.fill_assumption,
            )
            exit_price = estimate_fill_price(
                exit_snapshot.mark_price,
                exit_side,
                self.simulation_config.fill_assumption,
            )
            quantity = scenario.candidate.quantity
            gross_pnl = _spread_pnl(
                price_effect=order.price_effect,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
            )
            fees = round(
                self.simulation_config.commission_per_contract
                * sum(leg.quantity for leg in order.legs)
                * 2,
                2,
            )
            pnl = round(gross_pnl - fees, 2)
            trades.append(
                BacktestTrade(
                    trade_id=scenario.trade_id,
                    symbol=scenario.candidate.underlying,
                    strategy_name=scenario.candidate.strategy_name,
                    entry_date=scenario.entry_date,
                    exit_date=exit_snapshot.date,
                    pnl=pnl,
                    max_loss=scenario.candidate.total_max_loss() or 0.0,
                    exit_reason=exit_reason,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    fees=fees,
                    gross_pnl=gross_pnl,
                    dte_at_entry=scenario.candidate.dte,
                )
            )

        return BacktestResult(
            metrics=calculate_metrics(trades, self.initial_equity),
            trades=tuple(trades),
            skipped_trades=tuple(skipped),
        )


def _select_exit_snapshot(
    candidate: StrategyCandidate,
    snapshots: tuple[OptionPositionSnapshot, ...],
) -> tuple[OptionPositionSnapshot, str]:
    ordered = tuple(sorted(snapshots, key=lambda snapshot: snapshot.date))
    exit_plan = candidate.exit_plan
    credit_strategies = {"put_credit_spread", "call_credit_spread", "iron_condor"}
    price_effect = "credit" if candidate.strategy_name in credit_strategies else "debit"
    entry_value = candidate.expected_credit_or_debit / CONTRACT_MULTIPLIER
    total_max_profit = candidate.total_max_profit()
    total_quantity = candidate.quantity

    for snapshot in ordered:
        if snapshot.reason_code:
            return snapshot, snapshot.reason_code
        if exit_plan is not None:
            if price_effect == "credit":
                open_profit = (
                    entry_value - snapshot.mark_price
                ) * CONTRACT_MULTIPLIER * total_quantity
                if (
                    exit_plan.profit_target_pct is not None
                    and total_max_profit is not None
                    and open_profit >= total_max_profit * exit_plan.profit_target_pct
                ):
                    return snapshot, "profit_target"
                if (
                    exit_plan.stop_loss_multiple is not None
                    and snapshot.mark_price >= entry_value * exit_plan.stop_loss_multiple
                ):
                    return snapshot, "stop_loss"
            else:
                open_profit = (
                    snapshot.mark_price - entry_value
                ) * CONTRACT_MULTIPLIER * total_quantity
                entry_debit = entry_value * CONTRACT_MULTIPLIER * total_quantity
                if (
                    exit_plan.profit_target_pct is not None
                    and open_profit >= entry_debit * exit_plan.profit_target_pct
                ):
                    return snapshot, "profit_target"
                if (
                    exit_plan.stop_loss_pct is not None
                    and open_profit <= -(entry_debit * exit_plan.stop_loss_pct)
                ):
                    return snapshot, "stop_loss"
            if exit_plan.time_exit_dte is not None and snapshot.dte <= exit_plan.time_exit_dte:
                return snapshot, "time_exit"
        if snapshot.dte <= 0:
            return snapshot, "expiration"

    return ordered[-1], "last_snapshot"


def _spread_pnl(
    *,
    price_effect: str,
    entry_price: float,
    exit_price: float,
    quantity: int,
) -> float:
    if price_effect == "credit":
        return round((entry_price - exit_price) * CONTRACT_MULTIPLIER * quantity, 2)
    return round((exit_price - entry_price) * CONTRACT_MULTIPLIER * quantity, 2)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
