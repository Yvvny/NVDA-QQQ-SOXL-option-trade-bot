from __future__ import annotations

from dataclasses import dataclass

from trading_bot.config.settings import BotSettings
from trading_bot.core.enums import OrderType
from trading_bot.core.models import StrategyCandidate
from trading_bot.risk.portfolio import PortfolioState
from trading_bot.strategies.base import candidate_quality_score


@dataclass(frozen=True)
class CandidateRanking:
    candidate: StrategyCandidate
    opportunity_score: float
    reason_codes: tuple[str, ...]


class CandidateRanker:
    def __init__(self, settings: BotSettings) -> None:
        self.settings = settings

    def select(
        self,
        candidates: list[StrategyCandidate],
        *,
        risk_budget_base: float,
        portfolio_state: PortfolioState | None,
    ) -> list[StrategyCandidate]:
        if not self.settings.selection.enabled or not candidates:
            return candidates

        eligible = [
            ranking
            for candidate in candidates
            if (ranking := self._rank(candidate, risk_budget_base, portfolio_state)) is not None
        ]
        eligible.sort(key=lambda ranking: ranking.opportunity_score, reverse=True)
        if not eligible:
            return []

        threshold = self._min_score(portfolio_state)
        if eligible[0].opportunity_score < threshold:
            return []

        if len(eligible) >= 2 and not self._top_has_enough_separation(eligible[0], eligible[1]):
            return []

        return [eligible[0].candidate]

    def _rank(
        self,
        candidate: StrategyCandidate,
        risk_budget_base: float,
        portfolio_state: PortfolioState | None,
    ) -> CandidateRanking | None:
        hard_reasons = _hard_eligibility_reasons(candidate)
        if hard_reasons:
            return None

        risk_cap = self.settings.risk.per_trade_max_loss_cap(
            risk_budget_base,
            candidate.entry_score,
        )
        quality = candidate_quality_score(candidate, risk_cap, portfolio_state)
        reward_risk, spread_quality, diversification, _entry_score, risk_utilization = quality[:5]
        data_completeness = _data_completeness_score(candidate)

        opportunity_score = (
            min(candidate.entry_score, 100.0) * 0.45
            + min(reward_risk / 2.0, 1.0) * 15.0
            + spread_quality * 15.0
            + risk_utilization * 10.0
            + diversification * 10.0
            + data_completeness * 5.0
        )
        return CandidateRanking(
            candidate=candidate,
            opportunity_score=round(opportunity_score, 2),
            reason_codes=("ranked_candidate",),
        )

    def _min_score(self, portfolio_state: PortfolioState | None) -> float:
        if portfolio_state is not None and _preservation_mode_active(
            portfolio_state,
            self.settings,
        ):
            return self.settings.selection.preservation_min_opportunity_score
        return self.settings.selection.normal_min_opportunity_score

    def _top_has_enough_separation(
        self,
        top: CandidateRanking,
        runner_up: CandidateRanking,
    ) -> bool:
        gap = top.opportunity_score - runner_up.opportunity_score
        if gap >= self.settings.selection.min_top_score_gap:
            return True
        top_loss = top.candidate.total_max_loss()
        runner_up_loss = runner_up.candidate.total_max_loss()
        return (
            top_loss is not None
            and runner_up_loss is not None
            and top_loss <= runner_up_loss * self.settings.selection.lower_max_loss_tie_breaker_pct
        )


def _hard_eligibility_reasons(candidate: StrategyCandidate) -> tuple[str, ...]:
    reasons: list[str] = []
    if candidate.dte < 1:
        reasons.append("ranker_0dte_forbidden")
    if candidate.order_type == OrderType.MARKET:
        reasons.append("ranker_market_order_forbidden")
    if candidate.total_max_loss() is None:
        reasons.append("ranker_missing_max_loss")
    if candidate.exit_plan is None or not candidate.exit_plan.is_defined():
        reasons.append("ranker_missing_exit_plan")
    return tuple(reasons)


def _preservation_mode_active(
    portfolio_state: PortfolioState,
    settings: BotSettings,
) -> bool:
    if portfolio_state.account_equity < settings.account.assumed_equity:
        return True
    return (
        portfolio_state.total_open_max_loss
        >= portfolio_state.available_cash * settings.sizing.preservation_total_open_max_loss_pct
    )


def _data_completeness_score(candidate: StrategyCandidate) -> float:
    for leg in candidate.legs:
        if leg.contract.volume is None or leg.contract.open_interest is None:
            return 0.0
    return 1.0
