from __future__ import annotations

from collections.abc import Sequence

from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.strategies.base import StrategyEngine
from trading_bot.strategies.scoring import StrategyScoreResult
from trading_bot.strategies.short_premium import CONTRACT_MULTIPLIER


class NeutralRangeEngine(StrategyEngine):
    def generate_iron_condor(
        self,
        contracts: Sequence[OptionContract],
        underlying: str,
        dte: int,
        score: StrategyScoreResult,
    ) -> StrategyCandidate | None:
        if score.total < self.settings.strategy.min_entry_score:
            return None
        if "short_premium_blocked_crash_risk_off" in score.reason_codes:
            return None
        if not (self.settings.dte.neutral_range_min <= dte <= self.settings.dte.neutral_range_max):
            return None

        eligible = self._eligible_contracts(contracts)
        puts = sorted(
            (contract for contract in eligible if contract.option_type == OptionType.PUT),
            key=lambda contract: contract.strike,
        )
        calls = sorted(
            (contract for contract in eligible if contract.option_type == OptionType.CALL),
            key=lambda contract: contract.strike,
        )
        short_put = _short_delta_contract(
            puts,
            self.settings.delta.iron_condor_short_min_abs,
            self.settings.delta.iron_condor_short_max_abs,
            prefer_high_strike=True,
        )
        short_call = _short_delta_contract(
            calls,
            self.settings.delta.iron_condor_short_min_abs,
            self.settings.delta.iron_condor_short_max_abs,
            prefer_high_strike=False,
        )
        if short_put is None or short_call is None:
            return None

        long_put = _nearest_contract(
            puts,
            lambda contract: 1 <= short_put.strike - contract.strike <= 5,
            target_strike=short_put.strike,
        )
        long_call = _nearest_contract(
            calls,
            lambda contract: 1 <= contract.strike - short_call.strike <= 5,
            target_strike=short_call.strike,
        )
        if long_put is None or long_call is None:
            return None

        mids = (
            short_put.effective_mid(),
            long_put.effective_mid(),
            short_call.effective_mid(),
            long_call.effective_mid(),
        )
        if any(mid is None for mid in mids):
            return None
        short_put_mid, long_put_mid, short_call_mid, long_call_mid = mids
        credit = (short_put_mid + short_call_mid) - (long_put_mid + long_call_mid)
        put_width = short_put.strike - long_put.strike
        call_width = long_call.strike - short_call.strike
        width = max(put_width, call_width)
        if credit <= 0 or width <= 0:
            return None

        credit_pct_of_width = credit / width
        if not 0.20 <= credit_pct_of_width <= 0.35:
            return None

        max_profit = round(credit * CONTRACT_MULTIPLIER, 2)
        max_loss = round((width - credit) * CONTRACT_MULTIPLIER, 2)
        return StrategyCandidate(
            strategy_name="iron_condor",
            underlying=underlying,
            legs=(
                OptionLeg(short_put, OptionAction.SELL),
                OptionLeg(long_put, OptionAction.BUY),
                OptionLeg(short_call, OptionAction.SELL),
                OptionLeg(long_call, OptionAction.BUY),
            ),
            dte=dte,
            entry_score=score.total,
            max_profit=max_profit,
            max_loss=max_loss,
            expected_credit_or_debit=max_profit,
            reason_codes=score.reason_codes,
            exit_plan=ExitPlan(
                profit_target_pct=self.settings.strategy.iron_condor_profit_target,
                stop_loss_multiple=self.settings.strategy.credit_spread_stop_multiple,
                time_exit_dte=21,
                reason_codes=("iron_condor_standard_exit",),
            ),
            score_breakdown=score.breakdown,
            event_risk_blocked="event_risk_penalty" in score.reason_codes,
        )


def _short_delta_contract(
    contracts: Sequence[OptionContract],
    min_abs_delta: float,
    max_abs_delta: float,
    *,
    prefer_high_strike: bool,
) -> OptionContract | None:
    matches = [
        contract
        for contract in contracts
        if contract.delta is not None and min_abs_delta <= abs(contract.delta) <= max_abs_delta
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda contract: contract.strike, reverse=prefer_high_strike)[0]


def _nearest_contract(
    contracts: Sequence[OptionContract],
    predicate,
    target_strike: float,
) -> OptionContract | None:
    matches = [contract for contract in contracts if predicate(contract)]
    return min(matches, key=lambda contract: abs(contract.strike - target_strike), default=None)
