from __future__ import annotations

from dataclasses import replace

from trading_bot.config.settings import BotSettings, load_settings
from trading_bot.core.models import StrategyCandidate
from trading_bot.risk.portfolio import PortfolioState


class PositionSizer:
    def __init__(self, settings: BotSettings | None = None) -> None:
        self.settings = settings or load_settings()

    def size_candidate(
        self,
        candidate: StrategyCandidate,
        portfolio_state: PortfolioState,
    ) -> StrategyCandidate:
        if candidate.quantity != 1:
            return candidate
        if candidate.max_loss is None or candidate.max_loss <= 0:
            return candidate

        unit_max_loss = candidate.max_loss
        available_cash = portfolio_state.available_cash
        if available_cash <= 0:
            return candidate

        hard_cap = min(
            self.settings.risk.per_trade_max_loss_cap(
                risk_budget_base=available_cash,
                entry_score=candidate.entry_score,
            ),
            available_cash * self.settings.risk.total_open_max_loss_pct
            - portfolio_state.total_open_max_loss,
        )
        if candidate.underlying.upper() == "SOXL":
            hard_cap = min(hard_cap, self.settings.risk.soxl_per_trade_max_loss)
        if hard_cap < unit_max_loss:
            return candidate

        target_risk_pct, max_contracts = self._score_bucket(candidate.entry_score)
        target_risk = available_cash * target_risk_pct

        if portfolio_state.open_symbol_count(candidate.underlying) > 0:
            target_risk *= self.settings.sizing.same_symbol_multiplier
        if portfolio_state.open_strategy_count(candidate.strategy_name) > 0:
            target_risk *= self.settings.sizing.same_strategy_multiplier
        if (
            portfolio_state.total_open_max_loss
            > available_cash * self.settings.sizing.crowded_portfolio_threshold_pct
        ):
            target_risk *= self.settings.sizing.crowded_portfolio_multiplier

        desired_quantity = max(1, int(target_risk // unit_max_loss))
        quantity = min(desired_quantity, max_contracts, int(hard_cap // unit_max_loss))
        if quantity <= 1:
            return candidate
        return replace(candidate, quantity=quantity)

    def _score_bucket(self, entry_score: float) -> tuple[float, int]:
        if entry_score >= 80:
            return self.settings.sizing.high_score_target_risk_pct, self.settings.sizing.high_score_max_contracts
        if entry_score >= 65:
            return self.settings.sizing.good_score_target_risk_pct, self.settings.sizing.good_score_max_contracts
        return self.settings.sizing.low_score_target_risk_pct, self.settings.sizing.low_score_max_contracts
