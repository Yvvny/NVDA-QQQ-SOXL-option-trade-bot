from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from trading_bot.backtest.engine import (
    BacktestEngine,
    BacktestResult,
    BacktestScenario,
    BacktestSimulationConfig,
    OptionPositionSnapshot,
)
from trading_bot.core.enums import OptionAction, OptionType, OrderType
from trading_bot.core.models import (
    ExitPlan,
    OptionContract,
    OptionLeg,
    ScoreBreakdown,
    StrategyCandidate,
)
from trading_bot.risk.portfolio import OpenPosition, PortfolioState


@dataclass(frozen=True)
class ExitVariantSpec:
    code: str
    label: str
    exit_plan: ExitPlan


@dataclass(frozen=True)
class ExitMatrixVariantReport:
    code: str
    label: str
    result: BacktestResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "result": self.result.to_dict(),
        }


@dataclass(frozen=True)
class ExitMatrixReport:
    scenario_count: int
    variants: tuple[ExitMatrixVariantReport, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_count": self.scenario_count,
            "summary": [
                {
                    "code": variant.code,
                    "label": variant.label,
                    "number_of_trades": variant.result.metrics.number_of_trades,
                    "total_return": variant.result.metrics.total_return,
                    "ending_equity": variant.result.metrics.ending_equity,
                    "max_drawdown": variant.result.metrics.max_drawdown,
                    "profit_factor": variant.result.metrics.profit_factor,
                    "win_rate": variant.result.metrics.win_rate,
                    "expectancy_per_trade": variant.result.metrics.expectancy_per_trade,
                    "average_win_loss_ratio": variant.result.metrics.average_win_loss_ratio,
                    "worst_day": variant.result.metrics.worst_day,
                    "worst_week": variant.result.metrics.worst_week,
                    "exposure_time_trades": variant.result.metrics.exposure_time_trades,
                    "skipped_trades": len(variant.result.skipped_trades),
                }
                for variant in self.variants
            ],
            "variants": [variant.to_dict() for variant in self.variants],
        }

    def write_reports(self, output_dir: str | Path) -> Path:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        summary_path = output_path / "exit_matrix_summary.json"
        summary_path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        for variant in self.variants:
            variant_path = output_path / f"{variant.code.lower()}_backtest.json"
            variant.result.write_json_report(variant_path)
        return summary_path


DEFAULT_EXIT_VARIANTS: tuple[ExitVariantSpec, ...] = (
    ExitVariantSpec(
        code="E1",
        label="50% TP / 2.5x credit SL",
        exit_plan=ExitPlan(profit_target_pct=0.50, stop_loss_multiple=2.5),
    ),
    ExitVariantSpec(
        code="E2",
        label="50% TP / 2.0x credit SL",
        exit_plan=ExitPlan(profit_target_pct=0.50, stop_loss_multiple=2.0),
    ),
    ExitVariantSpec(
        code="E3",
        label="60% TP / 2.0x credit SL",
        exit_plan=ExitPlan(profit_target_pct=0.60, stop_loss_multiple=2.0),
    ),
    ExitVariantSpec(
        code="E4",
        label="40% TP / 1.8x credit SL",
        exit_plan=ExitPlan(profit_target_pct=0.40, stop_loss_multiple=1.8),
    ),
)


def run_exit_matrix(
    scenarios: list[BacktestScenario],
    *,
    initial_equity: float = 2000.0,
    simulation_config: BacktestSimulationConfig | None = None,
    variants: tuple[ExitVariantSpec, ...] = DEFAULT_EXIT_VARIANTS,
) -> ExitMatrixReport:
    variant_reports: list[ExitMatrixVariantReport] = []
    for variant in variants:
        engine = BacktestEngine(
            initial_equity=initial_equity,
            simulation_config=simulation_config,
        )
        variant_scenarios = [
            replace(
                scenario,
                candidate=replace(
                    scenario.candidate,
                    exit_plan=replace(
                        variant.exit_plan,
                        time_exit_dte=scenario.candidate.exit_plan.time_exit_dte
                        if scenario.candidate.exit_plan is not None
                        else None,
                        reason_codes=scenario.candidate.exit_plan.reason_codes
                        if scenario.candidate.exit_plan is not None
                        else (),
                    ),
                ),
            )
            for scenario in scenarios
        ]
        result = engine.run_scenarios(variant_scenarios)
        variant_reports.append(
            ExitMatrixVariantReport(code=variant.code, label=variant.label, result=result)
        )
    return ExitMatrixReport(scenario_count=len(scenarios), variants=tuple(variant_reports))


def load_scenarios_from_json(path: str | Path) -> list[BacktestScenario]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_scenarios = payload["scenarios"] if isinstance(payload, dict) else payload
    return [_parse_scenario(raw_scenario) for raw_scenario in raw_scenarios]


def _parse_scenario(payload: dict[str, Any]) -> BacktestScenario:
    return BacktestScenario(
        trade_id=str(payload["trade_id"]),
        candidate=_parse_candidate(payload["candidate"]),
        entry_date=date.fromisoformat(payload["entry_date"]),
        exit_snapshots=tuple(_parse_snapshot(item) for item in payload["exit_snapshots"]),
        portfolio_state=_parse_portfolio_state(payload.get("portfolio_state")),
        fill_ratio=float(payload.get("fill_ratio", 1.0)),
    )


def _parse_candidate(payload: dict[str, Any]) -> StrategyCandidate:
    score_breakdown_payload = payload.get("score_breakdown")
    return StrategyCandidate(
        strategy_name=str(payload["strategy_name"]),
        underlying=str(payload["underlying"]),
        legs=tuple(_parse_leg(item) for item in payload["legs"]),
        dte=int(payload["dte"]),
        entry_score=float(payload["entry_score"]),
        max_profit=_float_or_none(payload.get("max_profit")),
        max_loss=_float_or_none(payload.get("max_loss")),
        expected_credit_or_debit=float(payload["expected_credit_or_debit"]),
        reason_codes=tuple(str(item) for item in payload.get("reason_codes", ())),
        exit_plan=_parse_exit_plan(payload.get("exit_plan")),
        order_type=OrderType(str(payload.get("order_type", "limit"))),
        quantity=int(payload.get("quantity", 1)),
        score_breakdown=(
            ScoreBreakdown(**score_breakdown_payload) if score_breakdown_payload else None
        ),
        event_risk_blocked=bool(payload.get("event_risk_blocked", False)),
        liquidity_ok=bool(payload.get("liquidity_ok", True)),
        liquidity_warnings=tuple(str(item) for item in payload.get("liquidity_warnings", ())),
    )


def _parse_leg(payload: dict[str, Any]) -> OptionLeg:
    return OptionLeg(
        contract=_parse_contract(payload["contract"]),
        action=OptionAction(str(payload["action"])),
        quantity=int(payload.get("quantity", 1)),
    )


def _parse_contract(payload: dict[str, Any]) -> OptionContract:
    return OptionContract(
        symbol=str(payload["symbol"]),
        underlying=str(payload["underlying"]),
        expiration=date.fromisoformat(payload["expiration"]),
        strike=float(payload["strike"]),
        option_type=OptionType(str(payload["option_type"])),
        bid=_float_or_none(payload.get("bid")),
        ask=_float_or_none(payload.get("ask")),
        mid=_float_or_none(payload.get("mid")),
        delta=_float_or_none(payload.get("delta")),
        gamma=_float_or_none(payload.get("gamma")),
        theta=_float_or_none(payload.get("theta")),
        vega=_float_or_none(payload.get("vega")),
        iv=_float_or_none(payload.get("iv")),
        volume=_int_or_none(payload.get("volume")),
        open_interest=_int_or_none(payload.get("open_interest")),
        allow_missing_activity_data=bool(payload.get("allow_missing_activity_data", False)),
    )


def _parse_exit_plan(payload: dict[str, Any] | None) -> ExitPlan | None:
    if not payload:
        return None
    return ExitPlan(
        profit_target_pct=_float_or_none(payload.get("profit_target_pct")),
        stop_loss_pct=_float_or_none(payload.get("stop_loss_pct")),
        stop_loss_multiple=_float_or_none(payload.get("stop_loss_multiple")),
        time_exit_dte=_int_or_none(payload.get("time_exit_dte")),
        reason_codes=tuple(str(item) for item in payload.get("reason_codes", ())),
    )


def _parse_snapshot(payload: dict[str, Any]) -> OptionPositionSnapshot:
    return OptionPositionSnapshot(
        date=date.fromisoformat(payload["date"]),
        dte=int(payload["dte"]),
        mark_price=float(payload["mark_price"]),
        underlying_price=_float_or_none(payload.get("underlying_price")),
        reason_code=str(payload["reason_code"]) if payload.get("reason_code") else None,
    )


def _parse_portfolio_state(payload: dict[str, Any] | None) -> PortfolioState | None:
    if not payload:
        return None
    return PortfolioState(
        account_equity=float(payload["account_equity"]),
        daily_realized_pnl=float(payload.get("daily_realized_pnl", 0.0)),
        weekly_realized_pnl=float(payload.get("weekly_realized_pnl", 0.0)),
        consecutive_losses=int(payload.get("consecutive_losses", 0)),
        kill_switch_active=bool(payload.get("kill_switch_active", False)),
        open_positions=tuple(
            _parse_open_position(item) for item in payload.get("open_positions", ())
        ),
        new_trades_opened_today=int(payload.get("new_trades_opened_today", 0)),
        new_trades_opened_this_week=int(payload.get("new_trades_opened_this_week", 0)),
        available_cash=_float_or_none(payload.get("available_cash")),
    )


def _parse_open_position(payload: dict[str, Any]) -> OpenPosition:
    return OpenPosition(
        symbol=str(payload["symbol"]),
        strategy_name=str(payload["strategy_name"]),
        max_loss=float(payload["max_loss"]),
    )


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
