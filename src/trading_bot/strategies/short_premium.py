from __future__ import annotations

from collections.abc import Sequence

from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.strategies.base import StrategyEngine, candidate_quality_score
from trading_bot.strategies.scoring import StrategyScoreResult

CONTRACT_MULTIPLIER = 100


class ShortPremiumEngine(StrategyEngine):
    def generate_put_credit_spread(
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
                    candidates.append(candidate)
        return _select_preferred_candidate(
            candidates,
            underlying,
            score,
            self.settings,
            risk_budget_base=risk_budget_base,
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
