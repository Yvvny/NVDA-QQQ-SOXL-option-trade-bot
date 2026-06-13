from __future__ import annotations

from dataclasses import dataclass

from trading_bot.config.settings import BotSettings
from trading_bot.core.models import StrategyCandidate

CREDIT_SPREAD_STRATEGIES = frozenset(
    {"put_credit_spread", "call_credit_spread", "iron_condor"}
)
DEBIT_SPREAD_STRATEGIES = frozenset({"call_debit_spread", "put_debit_spread"})

EXIT_PLAN_MISSING = "exit_plan_missing"
EXIT_PLAN_PROFIT_TARGET_MISSING = "exit_plan_profit_target_missing"
EXIT_PLAN_STOP_MISSING = "exit_plan_stop_missing"
EXIT_PLAN_CREDIT_INVALID = "exit_plan_credit_invalid"
EXIT_PLAN_DEBIT_INVALID = "exit_plan_debit_invalid"
EXIT_PLAN_MAX_PROFIT_INVALID = "exit_plan_max_profit_invalid"
EXIT_PLAN_MAX_LOSS_INVALID = "exit_plan_max_loss_invalid"
EXIT_PLAN_PLANNED_LOSS_ABOVE_MAX_LOSS = "exit_plan_planned_loss_above_max_loss"
EXIT_PLAN_PLANNED_RR_BELOW_MIN = "exit_plan_planned_rr_below_min"
EXIT_PLAN_PLANNED_LOSS_PCT_TOO_HIGH = "exit_plan_planned_loss_pct_too_high"


@dataclass(frozen=True)
class ExitPlanQualityResult:
    planned_profit: float | None
    planned_loss: float | None
    planned_reward_risk: float | None
    planned_loss_pct_of_max_loss: float | None
    blocking_reasons: tuple[str, ...]
    warning_reasons: tuple[str, ...]

    @property
    def approved(self) -> bool:
        return not self.blocking_reasons


def exit_plan_quality(
    candidate: StrategyCandidate,
    settings: BotSettings,
) -> ExitPlanQualityResult:
    if not settings.strategy.exit_plan_quality_enabled:
        return ExitPlanQualityResult(None, None, None, None, (), ())
    if candidate.strategy_name in CREDIT_SPREAD_STRATEGIES:
        return _credit_spread_exit_plan_quality(candidate, settings)
    if candidate.strategy_name in DEBIT_SPREAD_STRATEGIES:
        return _debit_spread_exit_plan_quality(candidate, settings)
    return ExitPlanQualityResult(None, None, None, None, (), ())


def exit_plan_quality_sort_key(
    candidate: StrategyCandidate,
    settings: BotSettings,
) -> tuple[float, float]:
    result = exit_plan_quality(candidate, settings)
    planned_reward_risk = result.planned_reward_risk or 0.0
    planned_loss_pct = result.planned_loss_pct_of_max_loss
    return (
        planned_reward_risk,
        -(planned_loss_pct if planned_loss_pct is not None else 999.0),
    )


def _credit_spread_exit_plan_quality(
    candidate: StrategyCandidate,
    settings: BotSettings,
) -> ExitPlanQualityResult:
    blocking_reasons: list[str] = []
    warning_reasons: list[str] = []
    exit_plan = candidate.exit_plan
    total_max_loss = candidate.total_max_loss()
    credit = abs(candidate.total_expected_credit_or_debit())

    if exit_plan is None:
        blocking_reasons.append(EXIT_PLAN_MISSING)
        return _result(None, None, None, None, blocking_reasons, warning_reasons)
    if exit_plan.profit_target_pct is None:
        blocking_reasons.append(EXIT_PLAN_PROFIT_TARGET_MISSING)
    if exit_plan.stop_loss_multiple is None:
        blocking_reasons.append(EXIT_PLAN_STOP_MISSING)
    if credit <= 0:
        blocking_reasons.append(EXIT_PLAN_CREDIT_INVALID)
    if total_max_loss is None or total_max_loss <= 0:
        blocking_reasons.append(EXIT_PLAN_MAX_LOSS_INVALID)
    if blocking_reasons:
        return _result(None, None, None, None, blocking_reasons, warning_reasons)

    planned_profit = round(credit * exit_plan.profit_target_pct, 2)
    # Current paper close logic interprets stop_loss_multiple as PnL loss multiple of
    # entry credit, not close-price multiple.
    planned_loss = round(credit * exit_plan.stop_loss_multiple, 2)
    if planned_loss <= 0:
        blocking_reasons.append(EXIT_PLAN_STOP_MISSING)
        return _result(planned_profit, planned_loss, None, None, blocking_reasons, warning_reasons)

    planned_reward_risk = round(planned_profit / planned_loss, 4)
    planned_loss_pct = round(planned_loss / total_max_loss, 4)
    if planned_loss > total_max_loss:
        blocking_reasons.append(EXIT_PLAN_PLANNED_LOSS_ABOVE_MAX_LOSS)
    _add_threshold_reason(
        reason=EXIT_PLAN_PLANNED_RR_BELOW_MIN,
        failed=planned_reward_risk < settings.strategy.credit_spread_min_planned_reward_risk,
        settings=settings,
        blocking_reasons=blocking_reasons,
        warning_reasons=warning_reasons,
    )
    _add_threshold_reason(
        reason=EXIT_PLAN_PLANNED_LOSS_PCT_TOO_HIGH,
        failed=(
            planned_loss_pct
            > settings.strategy.credit_spread_max_planned_loss_pct_of_max_loss
        ),
        settings=settings,
        blocking_reasons=blocking_reasons,
        warning_reasons=warning_reasons,
    )
    return _result(
        planned_profit,
        planned_loss,
        planned_reward_risk,
        planned_loss_pct,
        blocking_reasons,
        warning_reasons,
    )


def _debit_spread_exit_plan_quality(
    candidate: StrategyCandidate,
    settings: BotSettings,
) -> ExitPlanQualityResult:
    blocking_reasons: list[str] = []
    warning_reasons: list[str] = []
    exit_plan = candidate.exit_plan
    total_max_loss = candidate.total_max_loss()
    total_max_profit = candidate.total_max_profit()
    debit = abs(candidate.total_expected_credit_or_debit())

    if exit_plan is None:
        blocking_reasons.append(EXIT_PLAN_MISSING)
        return _result(None, None, None, None, blocking_reasons, warning_reasons)
    if exit_plan.profit_target_pct is None:
        blocking_reasons.append(EXIT_PLAN_PROFIT_TARGET_MISSING)
    if exit_plan.stop_loss_pct is None:
        blocking_reasons.append(EXIT_PLAN_STOP_MISSING)
    if debit <= 0:
        blocking_reasons.append(EXIT_PLAN_DEBIT_INVALID)
    if total_max_loss is None or total_max_loss <= 0:
        blocking_reasons.append(EXIT_PLAN_MAX_LOSS_INVALID)
    if total_max_profit is None or total_max_profit <= 0:
        blocking_reasons.append(EXIT_PLAN_MAX_PROFIT_INVALID)
    if blocking_reasons:
        return _result(None, None, None, None, blocking_reasons, warning_reasons)

    max_profit_target = total_max_profit * exit_plan.profit_target_pct
    debit_return_target = (
        debit * settings.strategy.debit_spread_profit_target_pct_of_debit
    )
    planned_profit = round(min(max_profit_target, debit_return_target), 2)
    planned_loss = round(debit * exit_plan.stop_loss_pct, 2)
    if planned_loss <= 0:
        blocking_reasons.append(EXIT_PLAN_STOP_MISSING)
        return _result(planned_profit, planned_loss, None, None, blocking_reasons, warning_reasons)

    planned_reward_risk = round(planned_profit / planned_loss, 4)
    planned_loss_pct = round(planned_loss / total_max_loss, 4)
    if planned_loss > total_max_loss:
        blocking_reasons.append(EXIT_PLAN_PLANNED_LOSS_ABOVE_MAX_LOSS)
    _add_threshold_reason(
        reason=EXIT_PLAN_PLANNED_RR_BELOW_MIN,
        failed=planned_reward_risk < settings.strategy.debit_spread_min_planned_reward_risk,
        settings=settings,
        blocking_reasons=blocking_reasons,
        warning_reasons=warning_reasons,
    )
    _add_threshold_reason(
        reason=EXIT_PLAN_PLANNED_LOSS_PCT_TOO_HIGH,
        failed=planned_loss_pct
        > settings.strategy.debit_spread_max_planned_loss_pct_of_max_loss,
        settings=settings,
        blocking_reasons=blocking_reasons,
        warning_reasons=warning_reasons,
    )
    return _result(
        planned_profit,
        planned_loss,
        planned_reward_risk,
        planned_loss_pct,
        blocking_reasons,
        warning_reasons,
    )


def _add_threshold_reason(
    *,
    reason: str,
    failed: bool,
    settings: BotSettings,
    blocking_reasons: list[str],
    warning_reasons: list[str],
) -> None:
    if not failed:
        return
    if settings.strategy.exit_plan_quality_monitor_only:
        warning_reasons.append(reason)
    else:
        blocking_reasons.append(reason)


def _result(
    planned_profit: float | None,
    planned_loss: float | None,
    planned_reward_risk: float | None,
    planned_loss_pct_of_max_loss: float | None,
    blocking_reasons: list[str],
    warning_reasons: list[str],
) -> ExitPlanQualityResult:
    return ExitPlanQualityResult(
        planned_profit=planned_profit,
        planned_loss=planned_loss,
        planned_reward_risk=planned_reward_risk,
        planned_loss_pct_of_max_loss=planned_loss_pct_of_max_loss,
        blocking_reasons=_dedupe(blocking_reasons),
        warning_reasons=_dedupe(warning_reasons),
    )


def _dedupe(reasons: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for reason in reasons:
        if reason in seen:
            continue
        seen.add(reason)
        deduped.append(reason)
    return tuple(deduped)
