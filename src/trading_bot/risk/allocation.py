from __future__ import annotations

from dataclasses import dataclass

from trading_bot.config.settings import BotSettings
from trading_bot.core.models import RiskDecision, StrategyCandidate
from trading_bot.risk.engine import REASON_APPROVED, REASON_MISSING_MAX_LOSS

REASON_ALLOCATION_SYMBOL_CAP_EXCEEDED = "allocation_symbol_open_risk_cap_exceeded"
REASON_ALLOCATION_CLUSTER_CAP_EXCEEDED = "allocation_cluster_open_risk_cap_exceeded"
REASON_ALLOCATION_SYMBOL_REQUIRES_EXPERIMENT = "allocation_symbol_requires_experiment"
REASON_ALLOCATION_EXPERIMENTAL_BUDGET_EXCEEDED = (
    "allocation_experimental_budget_exceeded"
)
REASON_ALLOCATION_MAX_ACTIVE_EXPERIMENTS_EXCEEDED = (
    "allocation_max_active_experiments_exceeded"
)
REASON_ALLOCATION_MISSING_EXPERIMENT_METADATA = (
    "allocation_missing_experiment_metadata"
)


@dataclass(frozen=True)
class AllocationPosition:
    symbol: str
    strategy_name: str
    max_loss: float
    is_experiment: bool = False


def validate_symbol_allocation(
    candidate: StrategyCandidate,
    *,
    account_equity: float,
    open_positions: tuple[AllocationPosition, ...],
    settings: BotSettings,
    preservation_mode_active: bool,
) -> RiskDecision:
    total_max_loss = candidate.total_max_loss()
    if not settings.allocation.enabled:
        return _approved(total_max_loss, candidate.quantity)
    if settings.allocation.preservation_only and not preservation_mode_active:
        return _approved(total_max_loss, candidate.quantity)
    if total_max_loss is None:
        return RiskDecision(
            approved=False,
            reason_codes=(REASON_MISSING_MAX_LOSS,),
            max_loss=None,
            adjusted_size=None,
        )

    reason_codes: list[str] = []
    symbol = candidate.underlying.upper()
    equity = max(0.0, account_equity)
    symbol_open_risk = sum(
        position.max_loss for position in open_positions if position.symbol.upper() == symbol
    )
    symbol_cap = equity * _symbol_cap_pct(symbol, settings)
    if symbol_cap > 0 and symbol_open_risk + total_max_loss > symbol_cap:
        reason_codes.append(REASON_ALLOCATION_SYMBOL_CAP_EXCEEDED)

    cluster_symbols = _csv_symbols(settings.allocation.tech_beta_cluster_symbols)
    if symbol in cluster_symbols:
        cluster_open_risk = sum(
            position.max_loss
            for position in open_positions
            if position.symbol.upper() in cluster_symbols
        )
        cluster_cap = equity * settings.allocation.tech_beta_cluster_max_open_risk_pct
        if cluster_open_risk + total_max_loss > cluster_cap:
            reason_codes.append(REASON_ALLOCATION_CLUSTER_CAP_EXCEEDED)

    experiment = _candidate_is_experiment(candidate)
    if symbol in _csv_symbols(settings.allocation.experimental_only_symbols) and not experiment:
        reason_codes.append(REASON_ALLOCATION_SYMBOL_REQUIRES_EXPERIMENT)

    if experiment:
        active_experiments = sum(1 for position in open_positions if position.is_experiment)
        if active_experiments >= settings.allocation.max_active_experiments:
            reason_codes.append(REASON_ALLOCATION_MAX_ACTIVE_EXPERIMENTS_EXCEEDED)

        experiment_cap = equity * settings.allocation.experimental_budget_pct
        if total_max_loss > experiment_cap:
            reason_codes.append(REASON_ALLOCATION_EXPERIMENTAL_BUDGET_EXCEEDED)

        if not _candidate_has_experiment_metadata(candidate):
            reason_codes.append(REASON_ALLOCATION_MISSING_EXPERIMENT_METADATA)

    if reason_codes:
        return RiskDecision(
            approved=False,
            reason_codes=_dedupe(reason_codes),
            max_loss=total_max_loss,
            adjusted_size=None,
        )
    return _approved(total_max_loss, candidate.quantity)


def _approved(total_max_loss: float | None, quantity: int) -> RiskDecision:
    return RiskDecision(
        approved=True,
        reason_codes=(REASON_APPROVED,),
        max_loss=total_max_loss,
        adjusted_size=quantity,
    )


def _symbol_cap_pct(symbol: str, settings: BotSettings) -> float:
    if symbol == "QQQ":
        return settings.allocation.qqq_preservation_max_open_risk_pct
    if symbol == "NVDA":
        return settings.allocation.nvda_preservation_max_open_risk_pct
    if symbol == "SOXL":
        return settings.allocation.soxl_preservation_max_open_risk_pct
    return 0.0


def _candidate_is_experiment(candidate: StrategyCandidate) -> bool:
    return any(_is_experiment_reason(reason) for reason in candidate.reason_codes)


def _candidate_has_experiment_metadata(candidate: StrategyCandidate) -> bool:
    return candidate.exit_plan is not None and any(
        str(reason).startswith("experiment_hypothesis:") for reason in candidate.reason_codes
    )


def _is_experiment_reason(reason: str) -> bool:
    normalized = reason.lower()
    return (
        normalized in {"experiment", "experimental", "paper_experiment"}
        or normalized.startswith("experiment_")
    )


def _csv_symbols(raw_symbols: str) -> frozenset[str]:
    return frozenset(
        symbol.strip().upper() for symbol in raw_symbols.split(",") if symbol.strip()
    )


def _dedupe(reason_codes: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for reason_code in reason_codes:
        if reason_code in seen:
            continue
        seen.add(reason_code)
        deduped.append(reason_code)
    return tuple(deduped)
