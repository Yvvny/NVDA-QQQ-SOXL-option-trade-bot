from __future__ import annotations

from trading_bot.config.settings import BotSettings, load_settings
from trading_bot.core.enums import OptionAction, OptionType, OrderType
from trading_bot.core.models import OptionLeg, RiskDecision, StrategyCandidate
from trading_bot.risk.portfolio import PortfolioState

REASON_APPROVED = "approved"
REASON_KILL_SWITCH_ACTIVE = "kill_switch_active"
REASON_MISSING_LEGS = "missing_legs"
REASON_MISSING_EXIT_PLAN = "missing_exit_plan"
REASON_0DTE_FORBIDDEN = "0dte_forbidden"
REASON_MISSING_MAX_LOSS = "missing_max_loss"
REASON_INVALID_MAX_LOSS = "invalid_max_loss"
REASON_MARKET_ORDER_OPTIONS_FORBIDDEN = "market_orders_for_options_forbidden"
REASON_NAKED_SHORT_OPTION_FORBIDDEN = "naked_short_option_forbidden"
REASON_UNDEFINED_RISK_FORBIDDEN = "undefined_risk_forbidden"
REASON_PER_TRADE_MAX_LOSS_EXCEEDED = "per_trade_max_loss_exceeded"
REASON_SOXL_MAX_LOSS_EXCEEDED = "soxl_per_trade_max_loss_exceeded"
REASON_TOTAL_OPEN_MAX_LOSS_EXCEEDED = "total_open_max_loss_exceeded"
REASON_DAILY_LOSS_LIMIT_EXCEEDED = "daily_loss_limit_exceeded"
REASON_WEEKLY_LOSS_LIMIT_EXCEEDED = "weekly_loss_limit_exceeded"
REASON_CONSECUTIVE_LOSS_LIMIT_EXCEEDED = "max_consecutive_losses_exceeded"
REASON_MAX_NEW_TRADES_TODAY_EXCEEDED = "max_new_trades_per_day_exceeded"
REASON_MAX_NEW_TRADES_WEEK_EXCEEDED = "max_new_trades_per_week_exceeded"
REASON_SYMBOL_CONCENTRATION_EXCEEDED = "same_symbol_concentration_exceeded"
REASON_STRATEGY_CONCENTRATION_EXCEEDED = "same_strategy_concentration_exceeded"
REASON_EVENT_RISK_BLOCK = "event_risk_block"
REASON_LIQUIDITY_BLOCK = "liquidity_block"


class RiskEngine:
    def __init__(self, settings: BotSettings | None = None) -> None:
        self.settings = settings or load_settings()

    def evaluate(
        self,
        candidate: StrategyCandidate,
        portfolio_state: PortfolioState,
    ) -> RiskDecision:
        reason_codes: list[str] = []

        if portfolio_state.kill_switch.active:
            reason_codes.append(REASON_KILL_SWITCH_ACTIVE)
            reason_codes.extend(portfolio_state.kill_switch.reason_codes)

        if not candidate.legs:
            reason_codes.append(REASON_MISSING_LEGS)

        if candidate.exit_plan is None or not candidate.exit_plan.is_defined():
            reason_codes.append(REASON_MISSING_EXIT_PLAN)

        if (
            not self.settings.forbidden.allow_0dte
            and candidate.dte < self.settings.dte.forbidden_dte_min
        ):
            reason_codes.append(REASON_0DTE_FORBIDDEN)

        if candidate.max_loss is None:
            reason_codes.append(REASON_MISSING_MAX_LOSS)
        elif candidate.max_loss <= 0:
            reason_codes.append(REASON_INVALID_MAX_LOSS)

        if (
            not self.settings.forbidden.allow_market_orders_options
            and candidate.order_type == OrderType.MARKET
            and candidate.legs
        ):
            reason_codes.append(REASON_MARKET_ORDER_OPTIONS_FORBIDDEN)

        if not self.settings.forbidden.allow_naked_options and _has_unprotected_short_option(
            candidate.legs
        ):
            reason_codes.append(REASON_NAKED_SHORT_OPTION_FORBIDDEN)
            reason_codes.append(REASON_UNDEFINED_RISK_FORBIDDEN)

        if candidate.event_risk_blocked:
            reason_codes.append(REASON_EVENT_RISK_BLOCK)

        if not candidate.liquidity_ok:
            reason_codes.append(REASON_LIQUIDITY_BLOCK)
            reason_codes.extend(candidate.liquidity_warnings)

        if candidate.max_loss is not None and candidate.max_loss > 0:
            self._evaluate_loss_limits(candidate, portfolio_state, reason_codes)

        self._evaluate_portfolio_limits(candidate, portfolio_state, reason_codes)

        unique_reason_codes = _dedupe(reason_codes)
        approved = not unique_reason_codes
        return RiskDecision(
            approved=approved,
            reason_codes=(REASON_APPROVED,) if approved else unique_reason_codes,
            max_loss=candidate.max_loss,
            adjusted_size=candidate.quantity if approved else None,
        )

    def _evaluate_loss_limits(
        self,
        candidate: StrategyCandidate,
        portfolio_state: PortfolioState,
        reason_codes: list[str],
    ) -> None:
        per_trade_limit = (
            self.settings.risk.per_trade_max_loss_high_score
            if candidate.entry_score >= 80
            else self.settings.risk.per_trade_max_loss_default
        )
        if candidate.max_loss is not None and candidate.max_loss > per_trade_limit:
            reason_codes.append(REASON_PER_TRADE_MAX_LOSS_EXCEEDED)

        if (
            candidate.underlying.upper() == "SOXL"
            and candidate.max_loss is not None
            and candidate.max_loss > self.settings.risk.soxl_per_trade_max_loss
        ):
            reason_codes.append(REASON_SOXL_MAX_LOSS_EXCEEDED)

        max_total_open_loss = (
            portfolio_state.account_equity * self.settings.risk.total_open_max_loss_pct
        )
        projected_open_loss = portfolio_state.total_open_max_loss + (candidate.max_loss or 0.0)
        if projected_open_loss > max_total_open_loss:
            reason_codes.append(REASON_TOTAL_OPEN_MAX_LOSS_EXCEEDED)

    def _evaluate_portfolio_limits(
        self,
        candidate: StrategyCandidate,
        portfolio_state: PortfolioState,
        reason_codes: list[str],
    ) -> None:
        if portfolio_state.daily_realized_pnl <= -self.settings.risk.daily_loss_limit:
            reason_codes.append(REASON_DAILY_LOSS_LIMIT_EXCEEDED)

        if portfolio_state.weekly_realized_pnl <= -self.settings.risk.weekly_loss_limit:
            reason_codes.append(REASON_WEEKLY_LOSS_LIMIT_EXCEEDED)

        if portfolio_state.consecutive_losses >= self.settings.risk.max_consecutive_losses:
            reason_codes.append(REASON_CONSECUTIVE_LOSS_LIMIT_EXCEEDED)

        if portfolio_state.new_trades_today >= self.settings.risk.max_new_trades_per_day:
            reason_codes.append(REASON_MAX_NEW_TRADES_TODAY_EXCEEDED)

        if portfolio_state.new_trades_this_week >= self.settings.risk.max_new_trades_per_week:
            reason_codes.append(REASON_MAX_NEW_TRADES_WEEK_EXCEEDED)

        if (
            portfolio_state.open_symbol_count(candidate.underlying)
            >= self.settings.risk.max_same_symbol_open_positions
        ):
            reason_codes.append(REASON_SYMBOL_CONCENTRATION_EXCEEDED)

        if (
            portfolio_state.open_strategy_count(candidate.strategy_name)
            >= self.settings.risk.max_same_strategy_open_positions
        ):
            reason_codes.append(REASON_STRATEGY_CONCENTRATION_EXCEEDED)


def _has_unprotected_short_option(legs: tuple[OptionLeg, ...]) -> bool:
    short_legs = [leg for leg in legs if leg.action == OptionAction.SELL]
    long_legs = [leg for leg in legs if leg.action == OptionAction.BUY]

    for short_leg in short_legs:
        protected_quantity = sum(
            long_leg.quantity
            for long_leg in long_legs
            if _protects_short_leg(short_leg=short_leg, long_leg=long_leg)
        )
        if protected_quantity < short_leg.quantity:
            return True

    return False


def _protects_short_leg(short_leg: OptionLeg, long_leg: OptionLeg) -> bool:
    short_contract = short_leg.contract
    long_contract = long_leg.contract

    if short_contract.underlying.upper() != long_contract.underlying.upper():
        return False
    if long_contract.expiration < short_contract.expiration:
        return False
    if short_contract.option_type != long_contract.option_type:
        return False

    return short_contract.option_type in {OptionType.CALL, OptionType.PUT}


def _dedupe(reason_codes: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for reason_code in reason_codes:
        if reason_code in seen:
            continue
        seen.add(reason_code)
        deduped.append(reason_code)
    return tuple(deduped)
