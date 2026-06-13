from __future__ import annotations

from trading_bot.config.settings import BotSettings
from trading_bot.core.models import StrategyCandidate

DEBIT_SPREAD_STRATEGIES = frozenset({"call_debit_spread", "put_debit_spread"})
SHORT_CREDIT_SPREAD_STRATEGIES = frozenset({"put_credit_spread", "call_credit_spread"})

REWARD_RISK_MISSING_OR_INVALID = "reward_risk_missing_or_invalid"
REWARD_RISK_DEBIT_BELOW_MIN_THRESHOLD = "reward_risk_debit_below_min_threshold"
REWARD_RISK_CREDIT_MISSING_EXIT_GEOMETRY = "reward_risk_credit_missing_exit_geometry"
REWARD_RISK_CREDIT_PLANNED_RR_BELOW_MIN = "reward_risk_credit_planned_rr_below_min"
REWARD_RISK_CREDIT_PLANNED_RR_LOW_HIGH_QUALITY_OVERRIDE = (
    "reward_risk_credit_planned_rr_low_high_quality_override"
)

NON_BLOCKING_REWARD_RISK_REASONS = frozenset(
    {REWARD_RISK_CREDIT_PLANNED_RR_LOW_HIGH_QUALITY_OVERRIDE}
)


def planned_reward_risk_reasons(
    candidate: StrategyCandidate,
    settings: BotSettings,
) -> tuple[str, ...]:
    if candidate.strategy_name in DEBIT_SPREAD_STRATEGIES:
        return _debit_spread_reward_risk_reasons(candidate, settings)
    if candidate.strategy_name in SHORT_CREDIT_SPREAD_STRATEGIES:
        return _credit_spread_planned_reward_risk_reasons(candidate, settings)
    return ()


def blocking_planned_reward_risk_reasons(
    candidate: StrategyCandidate,
    settings: BotSettings,
) -> tuple[str, ...]:
    return tuple(
        reason
        for reason in planned_reward_risk_reasons(candidate, settings)
        if reason not in NON_BLOCKING_REWARD_RISK_REASONS
    )


def _debit_spread_reward_risk_reasons(
    candidate: StrategyCandidate,
    settings: BotSettings,
) -> tuple[str, ...]:
    if candidate.max_profit is None or candidate.max_loss is None or candidate.max_loss <= 0:
        return (REWARD_RISK_MISSING_OR_INVALID,)
    if candidate.max_profit / candidate.max_loss < settings.strategy.debit_spread_min_reward_risk:
        return (REWARD_RISK_DEBIT_BELOW_MIN_THRESHOLD,)
    return ()


def _credit_spread_planned_reward_risk_reasons(
    candidate: StrategyCandidate,
    settings: BotSettings,
) -> tuple[str, ...]:
    exit_plan = candidate.exit_plan
    credit = abs(candidate.expected_credit_or_debit or 0.0)
    profit_target_pct = exit_plan.profit_target_pct if exit_plan is not None else None
    stop_loss_multiple = exit_plan.stop_loss_multiple if exit_plan is not None else None
    if credit <= 0 or profit_target_pct is None or stop_loss_multiple is None:
        return (REWARD_RISK_CREDIT_MISSING_EXIT_GEOMETRY,)

    planned_risk = credit * stop_loss_multiple
    if planned_risk <= 0:
        return (REWARD_RISK_CREDIT_MISSING_EXIT_GEOMETRY,)

    planned_reward = credit * profit_target_pct
    planned_rr = planned_reward / planned_risk
    if planned_rr >= settings.strategy.credit_spread_min_planned_reward_risk:
        return ()
    if candidate.entry_score >= settings.strategy.credit_spread_high_quality_override_score:
        return (REWARD_RISK_CREDIT_PLANNED_RR_LOW_HIGH_QUALITY_OVERRIDE,)
    return (REWARD_RISK_CREDIT_PLANNED_RR_BELOW_MIN,)
