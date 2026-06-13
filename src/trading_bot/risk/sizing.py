from __future__ import annotations

from dataclasses import replace
from typing import Literal

from trading_bot.config.settings import BotSettings, load_settings
from trading_bot.core.models import StrategyCandidate
from trading_bot.risk.budget import build_risk_budget_snapshot
from trading_bot.risk.portfolio import PortfolioState

SizingMode = Literal["preservation", "normal"]


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

        mode = self._sizing_mode(portfolio_state)
        hard_cap = self._hard_cap(candidate, portfolio_state, mode)
        if candidate.underlying.upper() == "SOXL":
            hard_cap = min(hard_cap, self.settings.risk.soxl_per_trade_max_loss)
        if hard_cap < unit_max_loss:
            return candidate
        if mode == "preservation":
            return candidate

        target_risk_pct, max_contracts = self._score_bucket(candidate.entry_score, mode)
        target_risk = available_cash * target_risk_pct

        if portfolio_state.open_symbol_count(candidate.underlying) > 0:
            target_risk *= self.settings.sizing.same_symbol_multiplier
        if portfolio_state.open_strategy_count(candidate.strategy_name) > 0:
            target_risk *= self.settings.sizing.same_strategy_multiplier
        if (
            portfolio_state.total_open_max_loss
            > portfolio_state.account_equity
            * self.settings.sizing.crowded_portfolio_threshold_pct
        ):
            target_risk *= self.settings.sizing.crowded_portfolio_multiplier

        target_risk = min(target_risk, hard_cap)
        desired_quantity = max(1, int(target_risk // unit_max_loss))
        quantity = min(desired_quantity, max_contracts, int(hard_cap // unit_max_loss))
        if quantity <= 1:
            return candidate
        return replace(candidate, quantity=quantity)

    def _sizing_mode(self, portfolio_state: PortfolioState) -> SizingMode:
        if not self.settings.sizing.preservation_enabled:
            return "normal"
        starting_equity = self.settings.account.assumed_equity
        equity = portfolio_state.account_equity
        if starting_equity <= 0 or equity <= 0:
            return "preservation"
        drawdown_pct = max(0.0, (starting_equity - equity) / starting_equity)
        if drawdown_pct >= self.settings.sizing.preservation_drawdown_threshold_pct:
            return "preservation"
        if equity < starting_equity:
            return "preservation"
        if (
            portfolio_state.total_open_max_loss
            >= equity * self.settings.sizing.preservation_total_open_max_loss_pct
        ):
            return "preservation"
        if portfolio_state.consecutive_losses >= 2:
            return "preservation"
        return "normal"

    def _hard_cap(
        self,
        candidate: StrategyCandidate,
        portfolio_state: PortfolioState,
        mode: SizingMode,
    ) -> float:
        if mode == "preservation":
            per_trade_cap = min(
                self.settings.sizing.preservation_per_trade_max_loss_abs,
                portfolio_state.available_cash
                * self.settings.sizing.preservation_per_trade_max_loss_pct,
            )
            snapshot = build_risk_budget_snapshot(
                settings=self.settings,
                portfolio_state=portfolio_state,
                entry_score=candidate.entry_score,
                per_trade_max_loss_cap=per_trade_cap,
                total_open_max_loss_pct=(
                    self.settings.sizing.preservation_total_open_max_loss_pct
                ),
                preservation_mode_active=True,
            )
            return snapshot.effective_new_trade_max_loss_capacity

        snapshot = build_risk_budget_snapshot(
            settings=self.settings,
            portfolio_state=portfolio_state,
            entry_score=candidate.entry_score,
            preservation_mode_active=False,
        )
        return snapshot.effective_new_trade_max_loss_capacity

    def _score_bucket(self, entry_score: float, mode: SizingMode) -> tuple[float, int]:
        if mode == "preservation":
            return 0.0, self.settings.sizing.preservation_max_contracts
        if entry_score >= 80:
            return (
                self.settings.sizing.high_score_target_risk_pct,
                self.settings.sizing.high_score_max_contracts,
            )
        if entry_score >= 65:
            return (
                self.settings.sizing.good_score_target_risk_pct,
                self.settings.sizing.good_score_max_contracts,
            )
        return (
            self.settings.sizing.low_score_target_risk_pct,
            self.settings.sizing.low_score_max_contracts,
        )
