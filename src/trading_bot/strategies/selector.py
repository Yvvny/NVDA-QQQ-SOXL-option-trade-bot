from __future__ import annotations

from collections.abc import Iterable, Sequence

from trading_bot.config.settings import BotSettings, load_settings
from trading_bot.core.models import OptionContract, StrategyCandidate
from trading_bot.strategies.scoring import StrategyScoreInput, score_strategy_setup
from trading_bot.strategies.short_premium import ShortPremiumEngine
from trading_bot.strategies.trend_participation import TrendParticipationEngine


class StrategySelector:
    def __init__(self, settings: BotSettings | None = None) -> None:
        self.settings = settings or load_settings()
        self.short_premium = ShortPremiumEngine(self.settings)
        self.trend = TrendParticipationEngine(self.settings)

    def generate_candidates(
        self,
        contracts: Sequence[OptionContract],
        underlying: str,
        dte: int,
        score_inputs: Iterable[StrategyScoreInput],
    ) -> list[StrategyCandidate]:
        candidates: list[StrategyCandidate] = []
        for score_input in score_inputs:
            score = score_strategy_setup(score_input)
            candidate = self._generate_candidate(
                strategy_name=score_input.strategy_name,
                contracts=contracts,
                underlying=underlying,
                dte=dte,
                score=score,
            )
            if candidate is not None:
                candidates.append(candidate)
        return sorted(candidates, key=lambda candidate: candidate.entry_score, reverse=True)

    def _generate_candidate(self, strategy_name, contracts, underlying, dte, score):
        if strategy_name == "put_credit_spread":
            return self.short_premium.generate_put_credit_spread(contracts, underlying, dte, score)
        if strategy_name == "call_credit_spread":
            return self.short_premium.generate_call_credit_spread(contracts, underlying, dte, score)
        if strategy_name == "call_debit_spread":
            return self.trend.generate_call_debit_spread(contracts, underlying, dte, score)
        if strategy_name == "put_debit_spread":
            return self.trend.generate_put_debit_spread(contracts, underlying, dte, score)
        return None
