from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.risk.exit_plan_quality import exit_plan_quality_sort_key
from trading_bot.strategies.base import StrategyEngine, bid_ask_pct_of_mid, candidate_quality_score
from trading_bot.strategies.reward_risk import (
    blocking_planned_reward_risk_reasons,
    planned_reward_risk_reasons,
)
from trading_bot.strategies.scoring import StrategyScoreResult
from trading_bot.strategies.timing_filters import EntryTimingContext

CONTRACT_MULTIPLIER = 100


class ShortPremiumEngine(StrategyEngine):
    def generate_put_credit_spread(
        self,
        contracts: Sequence[OptionContract],
        underlying: str,
        dte: int,
        score: StrategyScoreResult,
        risk_budget_base: float | None = None,
        entry_timing: EntryTimingContext | None = None,
    ) -> StrategyCandidate | None:
        if score.total < self.settings.strategy.min_entry_score:
            return None
        if "short_premium_blocked_crash_risk_off" in score.reason_codes:
            return None
        if not (self.settings.dte.short_premium_min <= dte <= self.settings.dte.short_premium_max):
            return None

        puts = _sorted_by_strike(
            contract
            for contract in self._eligible_contracts(contracts)
            if contract.option_type == OptionType.PUT
        )
        short_puts = [
            contract
            for contract in puts
            if contract.delta is not None
            and self.settings.delta.short_premium_min_abs
            <= abs(contract.delta)
            <= self.settings.delta.short_premium_max_abs
        ]
        candidates: list[StrategyCandidate] = []
        for short_put in reversed(short_puts):
            long_puts = _matching_contracts(
                puts,
                predicate=lambda contract, short_strike=short_put.strike: (
                    1 <= short_strike - contract.strike <= 5
                ),
                target_strike=short_put.strike,
            )
            for long_put in long_puts:
                candidate = _credit_spread_candidate(
                    strategy_name="put_credit_spread",
                    underlying=underlying,
                    dte=dte,
                    short_leg=short_put,
                    long_leg=long_put,
                    score=score,
                    settings=self.settings,
                )
                if candidate is not None:
                    candidate = _candidate_with_planned_reward_risk_reasons(
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
            entry_timing=entry_timing,
        )

    def generate_call_credit_spread(
        self,
        contracts: Sequence[OptionContract],
        underlying: str,
        dte: int,
        score: StrategyScoreResult,
        risk_budget_base: float | None = None,
    ) -> StrategyCandidate | None:
        if score.total < self.settings.strategy.min_entry_score:
            return None
        if "short_premium_blocked_crash_risk_off" in score.reason_codes:
            return None
        if not (21 <= dte <= self.settings.dte.short_premium_max):
            return None

        calls = _sorted_by_strike(
            contract
            for contract in self._eligible_contracts(contracts)
            if contract.option_type == OptionType.CALL
        )
        short_calls = [
            contract
            for contract in calls
            if contract.delta is not None
            and self.settings.delta.short_premium_min_abs
            <= abs(contract.delta)
            <= self.settings.delta.short_premium_max_abs
        ]
        candidates: list[StrategyCandidate] = []
        for short_call in short_calls:
            long_calls = _matching_contracts(
                calls,
                predicate=lambda contract, short_strike=short_call.strike: (
                    1 <= contract.strike - short_strike <= 5
                ),
                target_strike=short_call.strike,
            )
            for long_call in long_calls:
                candidate = _credit_spread_candidate(
                    strategy_name="call_credit_spread",
                    underlying=underlying,
                    dte=dte,
                    short_leg=short_call,
                    long_leg=long_call,
                    score=score,
                    settings=self.settings,
                )
                if candidate is not None:
                    candidate = _candidate_with_planned_reward_risk_reasons(
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


def _credit_spread_candidate(
    strategy_name: str,
    underlying: str,
    dte: int,
    short_leg: OptionContract,
    long_leg: OptionContract | None,
    score: StrategyScoreResult,
    settings,
) -> StrategyCandidate | None:
    if long_leg is None:
        return None

    short_mid = short_leg.effective_mid()
    long_mid = long_leg.effective_mid()
    if short_mid is None or long_mid is None:
        return None

    credit = short_mid - long_mid
    width = abs(short_leg.strike - long_leg.strike)
    if credit <= 0 or width <= 0:
        return None

    credit_pct_of_width = credit / width
    if not (
        settings.strategy.credit_spread_min_pct_of_width
        <= credit_pct_of_width
        <= settings.strategy.credit_spread_max_pct_of_width
    ):
        return None

    max_profit = round(credit * CONTRACT_MULTIPLIER, 2)
    max_loss = round((width - credit) * CONTRACT_MULTIPLIER, 2)
    return StrategyCandidate(
        strategy_name=strategy_name,
        underlying=underlying,
        legs=(
            OptionLeg(contract=short_leg, action=OptionAction.SELL),
            OptionLeg(contract=long_leg, action=OptionAction.BUY),
        ),
        dte=dte,
        entry_score=score.total,
        max_profit=max_profit,
        max_loss=max_loss,
        expected_credit_or_debit=max_profit,
        reason_codes=score.reason_codes,
        exit_plan=ExitPlan(
            profit_target_pct=settings.strategy.credit_spread_profit_target,
            stop_loss_multiple=settings.strategy.credit_spread_stop_multiple,
            time_exit_dte=21,
            reason_codes=("credit_spread_standard_exit",),
        ),
        score_breakdown=score.breakdown,
        event_risk_blocked="event_risk_penalty" in score.reason_codes,
    )


def _sorted_by_strike(contracts) -> list[OptionContract]:
    return sorted(contracts, key=lambda contract: contract.strike)


def _matching_contracts(
    contracts: Sequence[OptionContract],
    predicate,
    target_strike: float,
) -> list[OptionContract]:
    matches = [contract for contract in contracts if predicate(contract)]
    return sorted(matches, key=lambda contract: abs(contract.strike - target_strike))


def _select_preferred_candidate(
    candidates: Sequence[StrategyCandidate],
    underlying: str,
    score: StrategyScoreResult,
    settings,
    risk_budget_base: float | None = None,
    entry_timing: EntryTimingContext | None = None,
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
    if (
        settings.strategy.qqq_put_credit_spread_quality_enabled
        and underlying.upper() == "QQQ"
        and all(candidate.strategy_name == "put_credit_spread" for candidate in pool)
    ):
        return _select_qqq_put_credit_candidate(
            pool,
            risk_cap=risk_cap,
            settings=settings,
            entry_timing=entry_timing,
        )
    return max(
        pool,
        key=lambda candidate: (
            candidate_quality_score(candidate, risk_cap),
            *exit_plan_quality_sort_key(candidate, settings),
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


def _candidate_with_planned_reward_risk_reasons(
    candidate: StrategyCandidate,
    settings,
) -> StrategyCandidate | None:
    if blocking_planned_reward_risk_reasons(candidate, settings):
        return None
    reasons = planned_reward_risk_reasons(candidate, settings)
    if not reasons:
        return candidate
    return replace(
        candidate,
        reason_codes=_dedupe_reason_codes((*candidate.reason_codes, *reasons)),
    )


def _select_qqq_put_credit_candidate(
    candidates: Sequence[StrategyCandidate],
    *,
    risk_cap: float,
    settings,
    entry_timing: EntryTimingContext | None,
) -> StrategyCandidate:
    scored = [
        (
            candidate,
            *qqq_put_credit_spread_quality_score(
                candidate=candidate,
                underlying_price=(
                    entry_timing.underlying_price if entry_timing is not None else None
                ),
                vwap=entry_timing.vwap if entry_timing is not None else None,
                ema20=None,
                atr=entry_timing.atr if entry_timing is not None else None,
                risk_cap=risk_cap,
                preferred_delta_max=settings.strategy.qqq_put_credit_spread_preferred_delta_max,
                min_atr_cushion=settings.strategy.qqq_put_credit_spread_min_atr_cushion,
                strong_atr_cushion=settings.strategy.qqq_put_credit_spread_strong_atr_cushion,
                preferred_width=settings.strategy.qqq_put_credit_spread_preferred_width,
                preferred_credit_pct_max=(
                    settings.strategy.qqq_put_credit_spread_preferred_credit_pct_max
                ),
            ),
        )
        for candidate in candidates
    ]
    selected, _, reasons = max(
        scored,
        key=lambda item: (
            item[1],
            candidate_quality_score(item[0], risk_cap),
            *exit_plan_quality_sort_key(item[0], settings),
            -(item[0].max_loss if item[0].max_loss is not None else float("inf")),
        ),
    )
    return replace(
        selected,
        reason_codes=_dedupe_reason_codes(
            (
                *selected.reason_codes,
                "qqq_pcs_quality_selector_active",
                *reasons,
            )
        ),
    )


def qqq_put_credit_spread_quality_score(
    *,
    candidate: StrategyCandidate,
    underlying_price: float | None,
    vwap: float | None,
    ema20: float | None,
    atr: float | None,
    risk_cap: float,
    preferred_delta_max: float = 0.20,
    min_atr_cushion: float = 1.0,
    strong_atr_cushion: float = 1.5,
    preferred_width: float = 2.0,
    preferred_credit_pct_max: float = 0.30,
) -> tuple[float, tuple[str, ...]]:
    score = 0.0
    reasons: list[str] = []
    short_put = _short_put_leg(candidate)
    width = _spread_width(candidate)

    if short_put is None:
        return -100.0, ("qqq_pcs_short_put_missing",)

    short_delta = short_put.contract.delta
    if short_delta is None:
        score -= 25
        reasons.append("qqq_pcs_short_delta_missing")
    else:
        abs_short_delta = abs(short_delta)
        if 0.16 <= abs_short_delta <= preferred_delta_max:
            score += 25
            reasons.append("qqq_pcs_short_delta_preferred")
        elif preferred_delta_max < abs_short_delta <= 0.25:
            score += 10
            reasons.append("qqq_pcs_short_delta_acceptable")
        else:
            score -= 25
            reasons.append("qqq_pcs_short_delta_outside_quality_band")

    short_put_strike = short_put.contract.strike
    if atr is not None and atr > 0 and underlying_price is not None:
        cushion_atr = (underlying_price - short_put_strike) / atr
        if cushion_atr >= strong_atr_cushion:
            score += 25
            reasons.append("qqq_pcs_atr_cushion_strong")
        elif cushion_atr >= min_atr_cushion:
            score += 15
            reasons.append("qqq_pcs_atr_cushion_acceptable")
        else:
            score -= 50
            reasons.append("qqq_pcs_atr_cushion_too_thin")
    else:
        reasons.append("qqq_pcs_atr_cushion_unavailable")

    if vwap is not None:
        if short_put_strike < vwap:
            score += 15
            reasons.append("qqq_pcs_short_strike_below_vwap")
        else:
            score -= 25
            reasons.append("qqq_pcs_short_strike_not_below_vwap")
    else:
        reasons.append("qqq_pcs_vwap_unavailable")

    if ema20 is not None:
        if short_put_strike < ema20:
            score += 10
            reasons.append("qqq_pcs_short_strike_below_ema20")
        else:
            score -= 15
            reasons.append("qqq_pcs_short_strike_not_below_ema20")
    else:
        reasons.append("qqq_pcs_ema20_unavailable")

    if width is None or width <= 0:
        score -= 25
        reasons.append("qqq_pcs_width_invalid")
    else:
        credit = candidate.expected_credit_or_debit / CONTRACT_MULTIPLIER
        credit_pct = credit / width
        if (
            0.18 <= credit_pct
            <= min(0.35, max(0.18, preferred_credit_pct_max))
        ):
            score += 15
            reasons.append("qqq_pcs_credit_pct_preferred")
        elif preferred_credit_pct_max < credit_pct <= 0.35:
            score += 5
            reasons.append("qqq_pcs_credit_pct_aggressive")
        else:
            score -= 10
            reasons.append("qqq_pcs_credit_pct_less_preferred")

        if width <= preferred_width:
            score += 10
            reasons.append("qqq_pcs_width_small_account_preferred")
        else:
            score -= 10
            reasons.append("qqq_pcs_width_less_preferred")

    liquidity_score, liquidity_reasons = _qqq_put_credit_liquidity_score(candidate)
    score += liquidity_score
    reasons.extend(liquidity_reasons)

    if candidate.max_loss is not None and candidate.max_loss <= risk_cap:
        score += 5
        reasons.append("qqq_pcs_within_preferred_risk_cap")
    else:
        score -= 25
        reasons.append("qqq_pcs_above_preferred_risk_cap")

    return score, _dedupe_reason_codes(reasons)


def _short_put_leg(candidate: StrategyCandidate) -> OptionLeg | None:
    for leg in candidate.legs:
        if leg.action == OptionAction.SELL and leg.contract.option_type == OptionType.PUT:
            return leg
    return None


def _spread_width(candidate: StrategyCandidate) -> float | None:
    strikes = [leg.contract.strike for leg in candidate.legs]
    if len(strikes) < 2:
        return None
    return max(strikes) - min(strikes)


def _qqq_put_credit_liquidity_score(candidate: StrategyCandidate) -> tuple[float, tuple[str, ...]]:
    spread_pcts = [
        spread_pct
        for leg in candidate.legs
        if (spread_pct := bid_ask_pct_of_mid(leg.contract)) is not None
    ]
    if not spread_pcts:
        return -10.0, ("qqq_pcs_liquidity_unavailable",)

    average_spread_pct = sum(spread_pcts) / len(spread_pcts)
    if average_spread_pct <= 0.08:
        return 15.0, ("qqq_pcs_liquidity_strong",)
    if average_spread_pct <= 0.12:
        return 8.0, ("qqq_pcs_liquidity_acceptable",)
    if average_spread_pct <= 0.15:
        return 1.0, ("qqq_pcs_liquidity_passable",)
    return -20.0, ("qqq_pcs_liquidity_weak",)


def _dedupe_reason_codes(reason_codes: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for reason_code in reason_codes:
        if reason_code in seen:
            continue
        seen.add(reason_code)
        deduped.append(reason_code)
    return tuple(deduped)
