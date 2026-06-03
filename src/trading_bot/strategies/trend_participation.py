from __future__ import annotations

from collections.abc import Sequence

from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.strategies.base import StrategyEngine, candidate_quality_score
from trading_bot.strategies.scoring import StrategyScoreResult
from trading_bot.strategies.short_premium import CONTRACT_MULTIPLIER


class TrendParticipationEngine(StrategyEngine):
    def generate_call_debit_spread(
        self,
        contracts: Sequence[OptionContract],
        underlying: str,
        dte: int,
        score: StrategyScoreResult,
        risk_budget_base: float | None = None,
    ) -> StrategyCandidate | None:
        if score.total < self.settings.strategy.min_entry_score:
            return None
        if not _trend_dte_ok(underlying, dte, self.settings):
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
    ) -> StrategyCandidate | None:
        if score.total < self.settings.strategy.min_entry_score:
            return None
        if not _trend_dte_ok(underlying, dte, self.settings):
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
        reason_codes=score.reason_codes,
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
