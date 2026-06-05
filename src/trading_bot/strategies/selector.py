from __future__ import annotations

from collections.abc import Iterable, Sequence

from trading_bot.config.settings import BotSettings, load_settings
from trading_bot.core.models import OptionContract, StrategyCandidate
from trading_bot.risk.portfolio import PortfolioState
from trading_bot.strategies.calendar_diagonal import CalendarDiagonalEngine
from trading_bot.strategies.base import candidate_quality_score
from trading_bot.strategies.neutral_range import NeutralRangeEngine
from trading_bot.strategies.scoring import StrategyScoreInput, score_strategy_setup
from trading_bot.strategies.short_premium import ShortPremiumEngine
from trading_bot.strategies.trend_participation import TrendParticipationEngine


class StrategySelector:
    def __init__(self, settings: BotSettings | None = None) -> None:
        self.settings = settings or load_settings()
        self.short_premium = ShortPremiumEngine(self.settings)
        self.neutral_range = NeutralRangeEngine(self.settings)
        self.trend = TrendParticipationEngine(self.settings)
        self.calendar_diagonal = CalendarDiagonalEngine(self.settings)

    def generate_candidates(
        self,
        contracts: Sequence[OptionContract],
        underlying: str,
        dte: int,
        score_inputs: Iterable[StrategyScoreInput],
        risk_budget_base: float | None = None,
        portfolio_state: PortfolioState | None = None,
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
                risk_budget_base=risk_budget_base,
                entry_timing=score_input.entry_timing,
            )
            if candidate is not None:
                candidates.append(candidate)
        return sorted(
            candidates,
            key=lambda candidate: (
                candidate.entry_score,
                candidate_quality_score(
                    candidate,
                    self.settings.risk.per_trade_max_loss_cap(
                        risk_budget_base or self.settings.account.assumed_equity,
                        candidate.entry_score,
                    ),
                    portfolio_state,
                ),
            ),
            reverse=True,
        )

    def _generate_candidate(
        self,
        strategy_name,
        contracts,
        underlying,
        dte,
        score,
        risk_budget_base: float | None = None,
        entry_timing=None,
    ):
        if strategy_name == "put_credit_spread":
            return self.short_premium.generate_put_credit_spread(
                contracts,
                underlying,
                dte,
                score,
                risk_budget_base=risk_budget_base,
            )
        if strategy_name == "call_credit_spread":
            return self.short_premium.generate_call_credit_spread(
                contracts,
                underlying,
                dte,
                score,
                risk_budget_base=risk_budget_base,
            )
        if strategy_name == "call_debit_spread":
            return self.trend.generate_call_debit_spread(
                contracts,
                underlying,
                dte,
                score,
                risk_budget_base=risk_budget_base,
                entry_timing=entry_timing,
            )
        if strategy_name == "put_debit_spread":
            return self.trend.generate_put_debit_spread(
                contracts,
                underlying,
                dte,
                score,
                risk_budget_base=risk_budget_base,
                entry_timing=entry_timing,
            )
        if strategy_name == "iron_condor":
            return self.neutral_range.generate_iron_condor(contracts, underlying, dte, score)
        if strategy_name == "calendar_spread":
            return self.calendar_diagonal.generate_calendar_spread(
                contracts, underlying, dte, score
            )
        if strategy_name == "diagonal_spread":
            return self.calendar_diagonal.generate_diagonal_spread(
                contracts, underlying, dte, score
            )
        return None
