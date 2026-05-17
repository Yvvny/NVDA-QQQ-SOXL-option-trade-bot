from __future__ import annotations

from collections.abc import Sequence

from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.strategies.base import StrategyEngine
from trading_bot.strategies.scoring import StrategyScoreResult
from trading_bot.strategies.short_premium import CONTRACT_MULTIPLIER


class TrendParticipationEngine(StrategyEngine):
    def generate_call_debit_spread(
        self,
        contracts: Sequence[OptionContract],
        underlying: str,
        dte: int,
        score: StrategyScoreResult,
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
        for long_call in long_calls:
            short_call = _nearest_contract(
                calls,
                predicate=lambda contract, long_strike=long_call.strike: (
                    contract.strike > long_strike
                    and contract.delta is not None
                    and self.settings.delta.trend_short_min_abs
                    <= abs(contract.delta)
                    <= self.settings.delta.trend_short_max_abs
                ),
            )
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
                return candidate
        return None

    def generate_put_debit_spread(
        self,
        contracts: Sequence[OptionContract],
        underlying: str,
        dte: int,
        score: StrategyScoreResult,
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
        for long_put in reversed(long_puts):
            short_put = _nearest_contract(
                puts,
                predicate=lambda contract, long_strike=long_put.strike: (
                    contract.strike < long_strike
                    and contract.delta is not None
                    and self.settings.delta.trend_short_min_abs
                    <= abs(contract.delta)
                    <= self.settings.delta.trend_short_max_abs
                ),
            )
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
                return candidate
        return None


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
    )


def _trend_dte_ok(underlying: str, dte: int, settings) -> bool:
    if underlying.upper() == "SOXL":
        return settings.dte.trend_soxl_min <= dte <= settings.dte.trend_soxl_max
    return settings.dte.trend_qqq_nvda_min <= dte <= settings.dte.trend_qqq_nvda_max


def _sorted_by_strike(contracts) -> list[OptionContract]:
    return sorted(contracts, key=lambda contract: contract.strike)


def _nearest_contract(contracts: Sequence[OptionContract], predicate) -> OptionContract | None:
    matches = [contract for contract in contracts if predicate(contract)]
    return min(matches, key=lambda contract: abs(contract.delta or 0.0), default=None)
