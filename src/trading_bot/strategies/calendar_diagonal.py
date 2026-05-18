from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.strategies.base import StrategyEngine
from trading_bot.strategies.scoring import StrategyScoreResult
from trading_bot.strategies.short_premium import CONTRACT_MULTIPLIER


class CalendarDiagonalEngine(StrategyEngine):
    def generate_calendar_spread(
        self,
        contracts: Sequence[OptionContract],
        underlying: str,
        front_dte: int,
        score: StrategyScoreResult,
        *,
        as_of: date | None = None,
    ) -> StrategyCandidate | None:
        if score.total < self.settings.strategy.min_entry_score:
            return None
        if not (
            self.settings.dte.calendar_front_min
            <= front_dte
            <= self.settings.dte.calendar_front_max
        ):
            return None

        front, back = _select_front_back_expirations(
            self._eligible_contracts(contracts),
            as_of=as_of or date.today(),
            front_dte=front_dte,
            back_min=self.settings.dte.calendar_back_min,
            back_max=self.settings.dte.calendar_back_max,
        )
        if not front or not back:
            return None

        front_options = _by_type(front, OptionType.CALL)
        back_options = _by_type(back, OptionType.CALL)
        pair = _same_strike_pair(front_options, back_options)
        if pair is None:
            return None

        short_front, long_back = pair
        return _calendar_or_diagonal_candidate(
            strategy_name="calendar_spread",
            underlying=underlying,
            dte=front_dte,
            short_front=short_front,
            long_back=long_back,
            score=score,
            settings=self.settings,
        )

    def generate_diagonal_spread(
        self,
        contracts: Sequence[OptionContract],
        underlying: str,
        front_dte: int,
        score: StrategyScoreResult,
        *,
        as_of: date | None = None,
    ) -> StrategyCandidate | None:
        if score.total < self.settings.strategy.min_entry_score:
            return None
        if not (
            self.settings.dte.calendar_front_min
            <= front_dte
            <= self.settings.dte.calendar_front_max
        ):
            return None

        front, back = _select_front_back_expirations(
            self._eligible_contracts(contracts),
            as_of=as_of or date.today(),
            front_dte=front_dte,
            back_min=self.settings.dte.calendar_back_min,
            back_max=self.settings.dte.calendar_back_max,
        )
        if not front or not back:
            return None

        front_calls = _by_type(front, OptionType.CALL)
        back_calls = _by_type(back, OptionType.CALL)
        long_back = _long_delta_contract(back_calls)
        if long_back is None:
            return None
        short_front = _nearest_contract(
            front_calls,
            lambda contract: contract.strike > long_back.strike
            and contract.delta is not None
            and self.settings.delta.trend_short_min_abs
            <= abs(contract.delta)
            <= self.settings.delta.trend_short_max_abs,
            target_strike=long_back.strike,
        )
        if short_front is None:
            return None

        return _calendar_or_diagonal_candidate(
            strategy_name="diagonal_spread",
            underlying=underlying,
            dte=front_dte,
            short_front=short_front,
            long_back=long_back,
            score=score,
            settings=self.settings,
        )


def _calendar_or_diagonal_candidate(
    *,
    strategy_name: str,
    underlying: str,
    dte: int,
    short_front: OptionContract,
    long_back: OptionContract,
    score: StrategyScoreResult,
    settings,
) -> StrategyCandidate | None:
    short_mid = short_front.effective_mid()
    long_mid = long_back.effective_mid()
    if short_mid is None or long_mid is None:
        return None

    debit = long_mid - short_mid
    if debit <= 0:
        return None

    max_loss = round(debit * CONTRACT_MULTIPLIER, 2)
    return StrategyCandidate(
        strategy_name=strategy_name,
        underlying=underlying,
        legs=(
            OptionLeg(short_front, OptionAction.SELL),
            OptionLeg(long_back, OptionAction.BUY),
        ),
        dte=dte,
        entry_score=score.total,
        max_profit=None,
        max_loss=max_loss,
        expected_credit_or_debit=max_loss,
        reason_codes=score.reason_codes,
        exit_plan=ExitPlan(
            profit_target_pct=settings.strategy.calendar_profit_target,
            stop_loss_pct=settings.strategy.calendar_stop_loss,
            time_exit_dte=3,
            reason_codes=(f"{strategy_name}_standard_exit",),
        ),
        score_breakdown=score.breakdown,
        event_risk_blocked="event_risk_penalty" in score.reason_codes,
    )


def _select_front_back_expirations(
    contracts: Sequence[OptionContract],
    *,
    as_of: date,
    front_dte: int,
    back_min: int,
    back_max: int,
) -> tuple[tuple[OptionContract, ...], tuple[OptionContract, ...]]:
    by_expiration: dict[date, list[OptionContract]] = {}
    for contract in contracts:
        by_expiration.setdefault(contract.expiration, []).append(contract)

    front_expiration = min(
        (
            expiration
            for expiration in by_expiration
            if abs((expiration - as_of).days - front_dte) <= 3
        ),
        key=lambda expiration: abs((expiration - as_of).days - front_dte),
        default=None,
    )
    back_expiration = min(
        (
            expiration
            for expiration in by_expiration
            if back_min <= (expiration - as_of).days <= back_max
        ),
        key=lambda expiration: abs((expiration - as_of).days - ((back_min + back_max) / 2)),
        default=None,
    )
    if front_expiration is None or back_expiration is None:
        return (), ()
    if back_expiration <= front_expiration:
        return (), ()
    return tuple(by_expiration[front_expiration]), tuple(by_expiration[back_expiration])


def _by_type(
    contracts: Sequence[OptionContract],
    option_type: OptionType,
) -> list[OptionContract]:
    return sorted(
        (contract for contract in contracts if contract.option_type == option_type),
        key=lambda contract: contract.strike,
    )


def _same_strike_pair(
    front_options: Sequence[OptionContract],
    back_options: Sequence[OptionContract],
) -> tuple[OptionContract, OptionContract] | None:
    back_by_strike = {contract.strike: contract for contract in back_options}
    pairs = [
        (front, back_by_strike[front.strike])
        for front in front_options
        if front.strike in back_by_strike
    ]
    if not pairs:
        return None
    return min(pairs, key=lambda pair: abs(pair[0].delta or 0.50))


def _long_delta_contract(contracts: Sequence[OptionContract]) -> OptionContract | None:
    matches = [
        contract
        for contract in contracts
        if contract.delta is not None and 0.45 <= abs(contract.delta) <= 0.65
    ]
    return min(matches, key=lambda contract: abs(abs(contract.delta or 0) - 0.55), default=None)


def _nearest_contract(
    contracts: Sequence[OptionContract],
    predicate,
    target_strike: float,
) -> OptionContract | None:
    matches = [contract for contract in contracts if predicate(contract)]
    return min(matches, key=lambda contract: abs(contract.strike - target_strike), default=None)
