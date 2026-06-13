from __future__ import annotations

from dataclasses import dataclass

from trading_bot.config.settings import BotSettings
from trading_bot.risk.portfolio import PortfolioState


@dataclass(frozen=True)
class RiskBudgetSnapshot:
    starting_equity: float
    current_equity: float
    available_cash: float
    open_positions_count: int
    existing_open_max_loss: float
    configured_per_trade_max_loss_cap: float
    configured_total_open_max_loss_cap: float
    required_cash_reserve: float
    preservation_mode_active: bool
    remaining_total_risk_budget: float
    remaining_cash_capacity: float
    effective_new_trade_max_loss_capacity: float


def build_risk_budget_snapshot(
    *,
    settings: BotSettings,
    portfolio_state: PortfolioState,
    entry_score: float,
    per_trade_max_loss_cap: float | None = None,
    total_open_max_loss_pct: float | None = None,
    preservation_mode_active: bool = False,
) -> RiskBudgetSnapshot:
    """Return the canonical capacity available for one new defined-risk trade."""

    current_equity = max(0.0, portfolio_state.account_equity)
    available_cash = max(0.0, portfolio_state.available_cash)
    existing_open_max_loss = max(0.0, portfolio_state.total_open_max_loss)

    configured_per_trade_cap = (
        per_trade_max_loss_cap
        if per_trade_max_loss_cap is not None
        else settings.risk.per_trade_max_loss_cap(
            risk_budget_base=available_cash,
            entry_score=entry_score,
        )
    )
    configured_per_trade_cap = max(0.0, configured_per_trade_cap)

    total_risk_pct = (
        settings.risk.total_open_max_loss_pct
        if total_open_max_loss_pct is None
        else total_open_max_loss_pct
    )
    configured_total_cap = max(0.0, available_cash * total_risk_pct)
    required_cash_reserve = max(0.0, available_cash * settings.risk.min_account_cash_buffer_pct)
    remaining_total_risk_budget = max(0.0, configured_total_cap - existing_open_max_loss)
    remaining_cash_capacity = max(0.0, available_cash - required_cash_reserve)
    effective_capacity = max(
        0.0,
        min(
            configured_per_trade_cap,
            remaining_total_risk_budget,
            remaining_cash_capacity,
        ),
    )

    return RiskBudgetSnapshot(
        starting_equity=settings.account.assumed_equity,
        current_equity=current_equity,
        available_cash=available_cash,
        open_positions_count=len(portfolio_state.open_positions),
        existing_open_max_loss=existing_open_max_loss,
        configured_per_trade_max_loss_cap=round(configured_per_trade_cap, 2),
        configured_total_open_max_loss_cap=round(configured_total_cap, 2),
        required_cash_reserve=round(required_cash_reserve, 2),
        preservation_mode_active=preservation_mode_active,
        remaining_total_risk_budget=round(remaining_total_risk_budget, 2),
        remaining_cash_capacity=round(remaining_cash_capacity, 2),
        effective_new_trade_max_loss_capacity=round(effective_capacity, 2),
    )
