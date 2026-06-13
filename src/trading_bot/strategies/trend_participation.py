from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.risk.exit_plan_quality import exit_plan_quality_sort_key
from trading_bot.strategies.base import StrategyEngine, candidate_quality_score
from trading_bot.strategies.reward_risk import (
    blocking_planned_reward_risk_reasons,
    planned_reward_risk_reasons,
)
from trading_bot.strategies.scoring import StrategyScoreResult
from trading_bot.strategies.short_premium import CONTRACT_MULTIPLIER
from trading_bot.strategies.timing_filters import EntryTimingContext, evaluate_entry_timing


class TrendParticipationEngine(StrategyEngine):
    def generate_call_debit_spread(
        self,
        contracts: Sequence[OptionContract],
        underlying: str,
        dte: int,
        score: StrategyScoreResult,
        risk_budget_base: float | None = None,
        entry_timing: EntryTimingContext | None = None,
        iv_rank: float | None = None,
    ) -> StrategyCandidate | None:
        if score.total < self.settings.strategy.min_entry_score:
            return None
        if not _trend_dte_ok(underlying, dte, self.settings):
            return None
        if nvda_debit_spread_pre_candidate_reasons(
            underlying=underlying,
            strategy_name="call_debit_spread",
            score=score,
            iv_rank=iv_rank,
            settings=self.settings,
        ):
            return None
        timing_decision = evaluate_entry_timing(
            strategy_name="call_debit_spread",
            score_reason_codes=score.reason_codes,
            context=entry_timing,
            settings=self.settings,
        )
        if not timing_decision.approved:
            return None

        calls = _sorted_by_strike(
            contract
            for contract in self._eligible_contracts(contracts)
            if contract.option_type == OptionType.CALL
        )
        long_calls = [
            contract
            for contract in calls
            if contract.delta is not None
            and self.settings.delta.trend_long_min_abs
            <= abs(contract.delta)
            <= self.settings.delta.trend_long_max_abs
        ]
        candidates: list[StrategyCandidate] = []
        for long_call in long_calls:
            short_calls = _matching_contracts(
                calls,
                predicate=lambda contract, long_strike=long_call.strike: (
                    contract.strike > long_strike
                    and contract.delta is not None
                    and self.settings.delta.trend_short_min_abs
                    <= abs(contract.delta)
                    <= self.settings.delta.trend_short_max_abs
                ),
            )
            for short_call in short_calls:
                candidate = _debit_spread_candidate(
                    strategy_name="call_debit_spread",
                    underlying=underlying,
                    dte=dte,
                    long_leg=long_call,
                    short_leg=short_call,
                    score=score,
                    settings=self.settings,
                    timing_reason_codes=timing_decision.reason_codes,
                )
                if candidate is not None:
                    candidate = _candidate_with_planned_reward_risk_reasons(
                        candidate,
                        self.settings,
                    )
                    if candidate is not None:
                        candidate = _candidate_with_nvda_debit_reasons(
                            candidate,
                            self.settings,
                        )
                    if candidate is not None:
                        candidates.append(candidate)
        return _select_preferred_candidate(
            candidates,
            underlying,
            score,
            self.settings,
            risk_budget_base=risk_budget_base,
        )

    def generate_put_debit_spread(
        self,
        contracts: Sequence[OptionContract],
        underlying: str,
        dte: int,
        score: StrategyScoreResult,
        risk_budget_base: float | None = None,
        entry_timing: EntryTimingContext | None = None,
        iv_rank: float | None = None,
    ) -> StrategyCandidate | None:
        if score.total < self.settings.strategy.min_entry_score:
            return None
        if not _trend_dte_ok(underlying, dte, self.settings):
            return None
        if nvda_debit_spread_pre_candidate_reasons(
            underlying=underlying,
            strategy_name="put_debit_spread",
            score=score,
            iv_rank=iv_rank,
            settings=self.settings,
        ):
            return None
        timing_decision = evaluate_entry_timing(
            strategy_name="put_debit_spread",
            score_reason_codes=score.reason_codes,
            context=entry_timing,
            settings=self.settings,
        )
        if not timing_decision.approved:
            return None

        puts = _sorted_by_strike(
            contract
            for contract in self._eligible_contracts(contracts)
            if contract.option_type == OptionType.PUT
        )
        long_puts = [
            contract
            for contract in puts
            if contract.delta is not None
            and self.settings.delta.trend_long_min_abs
            <= abs(contract.delta)
            <= self.settings.delta.trend_long_max_abs
        ]
        candidates: list[StrategyCandidate] = []
        for long_put in reversed(long_puts):
            short_puts = _matching_contracts(
                puts,
                predicate=lambda contract, long_strike=long_put.strike: (
                    contract.strike < long_strike
                    and contract.delta is not None
                    and self.settings.delta.trend_short_min_abs
                    <= abs(contract.delta)
                    <= self.settings.delta.trend_short_max_abs
                ),
            )
            for short_put in short_puts:
                candidate = _debit_spread_candidate(
                    strategy_name="put_debit_spread",
                    underlying=underlying,
                    dte=dte,
                    long_leg=long_put,
                    short_leg=short_put,
                    score=score,
                    settings=self.settings,
                    timing_reason_codes=timing_decision.reason_codes,
                )
                if candidate is not None:
                    candidate = _candidate_with_planned_reward_risk_reasons(
                        candidate,
                        self.settings,
                    )
                    if candidate is not None:
                        candidate = _candidate_with_nvda_debit_reasons(
                            candidate,
                            self.settings,
                        )
                    if candidate is not None:
                        candidates.append(candidate)
        return _select_preferred_candidate(
            candidates,
            underlying,
            score,
            self.settings,
            risk_budget_base=risk_budget_base,
        )


def _debit_spread_candidate(
    strategy_name: str,
    underlying: str,
    dte: int,
    long_leg: OptionContract,
    short_leg: OptionContract | None,
    score: StrategyScoreResult,
    settings,
    timing_reason_codes: tuple[str, ...] = (),
) -> StrategyCandidate | None:
    if short_leg is None:
        return None

    long_mid = long_leg.effective_mid()
    short_mid = short_leg.effective_mid()
    if long_mid is None or short_mid is None:
        return None

    debit = long_mid - short_mid
    width = abs(short_leg.strike - long_leg.strike)
    if debit <= 0 or width <= 0 or debit >= width:
        return None

    max_profit = round((width - debit) * CONTRACT_MULTIPLIER, 2)
    max_loss = round(debit * CONTRACT_MULTIPLIER, 2)
    if max_profit / max_loss < 1.2:
        return None

    return StrategyCandidate(
        strategy_name=strategy_name,
        underlying=underlying,
        legs=(
            OptionLeg(contract=long_leg, action=OptionAction.BUY),
            OptionLeg(contract=short_leg, action=OptionAction.SELL),
        ),
        dte=dte,
        entry_score=score.total,
        max_profit=max_profit,
        max_loss=max_loss,
        expected_credit_or_debit=round(debit * CONTRACT_MULTIPLIER, 2),
        reason_codes=(*score.reason_codes, *timing_reason_codes),
        exit_plan=ExitPlan(
            profit_target_pct=settings.strategy.debit_spread_profit_target,
            stop_loss_pct=settings.strategy.debit_spread_stop_loss,
            reason_codes=("debit_spread_standard_exit",),
        ),
        score_breakdown=score.breakdown,
        event_risk_blocked="event_risk_penalty" in score.reason_codes,
    )


def _trend_dte_ok(underlying: str, dte: int, settings) -> bool:
    if underlying.upper() == "SOXL":
        return settings.dte.trend_soxl_min <= dte <= settings.dte.trend_soxl_max
    return settings.dte.trend_qqq_nvda_min <= dte <= settings.dte.trend_qqq_nvda_max


def _sorted_by_strike(contracts) -> list[OptionContract]:
    return sorted(contracts, key=lambda contract: contract.strike)


def _matching_contracts(contracts: Sequence[OptionContract], predicate) -> list[OptionContract]:
    matches = [contract for contract in contracts if predicate(contract)]
    return sorted(matches, key=lambda contract: abs(contract.delta or 0.0))


def _select_preferred_candidate(
    candidates: Sequence[StrategyCandidate],
    underlying: str,
    score: StrategyScoreResult,
    settings,
    risk_budget_base: float | None = None,
) -> StrategyCandidate | None:
    if not candidates:
        return None
    risk_cap = _preferred_max_loss_cap(underlying, score, settings, risk_budget_base)
    within_cap = [
        candidate
        for candidate in candidates
        if candidate.max_loss is not None and candidate.max_loss <= risk_cap
    ]
    pool = within_cap or list(candidates)
    return max(
        pool,
        key=lambda candidate: (
            *exit_plan_quality_sort_key(candidate, settings),
            candidate_quality_score(candidate, risk_cap),
            -(candidate.max_loss if candidate.max_loss is not None else float("inf")),
        ),
    )


def _preferred_max_loss_cap(
    underlying: str,
    score: StrategyScoreResult,
    settings,
    risk_budget_base: float | None = None,
) -> float:
    cap = settings.risk.per_trade_max_loss_cap(
        risk_budget_base=risk_budget_base or settings.account.assumed_equity,
        entry_score=score.total,
    )
    if underlying.upper() == "SOXL":
        return min(cap, settings.risk.soxl_per_trade_max_loss)
    return cap


def nvda_debit_spread_pre_candidate_reasons(
    *,
    underlying: str,
    strategy_name: str,
    score: StrategyScoreResult,
    iv_rank: float | None,
    settings,
) -> tuple[str, ...]:
    if underlying.upper() != "NVDA" or strategy_name not in {
        "call_debit_spread",
        "put_debit_spread",
    }:
        return ()

    if not settings.strategy.nvda_debit_spread_experimental_enabled:
        return ("nvda_debit_spread_disabled_by_default",)

    reasons: list[str] = []
    if score.total < settings.strategy.nvda_debit_spread_min_entry_score:
        reasons.append("nvda_debit_spread_score_below_80")
    if "event_risk_penalty" in score.reason_codes:
        reasons.append("nvda_debit_spread_event_risk_penalty")
    if iv_rank is None:
        reasons.append("nvda_debit_spread_iv_missing")
    elif iv_rank > settings.strategy.nvda_debit_spread_max_iv_rank:
        reasons.append("nvda_debit_spread_iv_too_high")
    return tuple(reasons)


def _candidate_with_planned_reward_risk_reasons(
    candidate: StrategyCandidate,
    settings,
) -> StrategyCandidate | None:
    if blocking_planned_reward_risk_reasons(candidate, settings):
        return None
    reasons = planned_reward_risk_reasons(candidate, settings)
    if not reasons:
        return candidate
    return replace(candidate, reason_codes=(*candidate.reason_codes, *reasons))


def _candidate_with_nvda_debit_reasons(
    candidate: StrategyCandidate,
    settings,
) -> StrategyCandidate | None:
    if candidate.underlying.upper() != "NVDA" or candidate.strategy_name not in {
        "call_debit_spread",
        "put_debit_spread",
    }:
        return candidate

    reasons = _nvda_debit_spread_candidate_reasons(candidate, settings)
    if reasons:
        return None
    return replace(
        candidate,
        reason_codes=(
            *candidate.reason_codes,
            "nvda_debit_spread_experimental_gate_passed",
        ),
    )


def _nvda_debit_spread_candidate_reasons(
    candidate: StrategyCandidate,
    settings,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if (
        candidate.max_profit is None
        or candidate.max_loss is None
        or candidate.max_loss <= 0
        or candidate.max_profit / candidate.max_loss
        < settings.strategy.nvda_debit_spread_min_reward_risk
    ):
        reasons.append("nvda_debit_spread_reward_risk_below_1_5")

    width = _spread_width(candidate)
    if width is None or width > settings.strategy.nvda_debit_spread_max_width:
        reasons.append("nvda_debit_spread_width_too_wide")
    return tuple(reasons)


def _spread_width(candidate: StrategyCandidate) -> float | None:
    strikes = [leg.contract.strike for leg in candidate.legs]
    if len(strikes) < 2:
        return None
    return max(strikes) - min(strikes)
