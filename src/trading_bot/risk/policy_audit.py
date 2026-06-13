from __future__ import annotations

from trading_bot.config.settings import BotSettings
from trading_bot.core.enums import OptionAction, OrderType
from trading_bot.core.models import RiskDecision, StrategyCandidate
from trading_bot.risk.engine import REASON_APPROVED

REASON_POLICY_NOT_PAPER_ONLY = "policy_not_paper_only"
REASON_POLICY_0DTE_FORBIDDEN = "policy_0dte_forbidden"
REASON_POLICY_MARKET_ORDER_FORBIDDEN = "policy_market_order_forbidden"
REASON_POLICY_MISSING_MAX_LOSS = "policy_missing_max_loss"
REASON_POLICY_INVALID_MAX_LOSS = "policy_invalid_max_loss"
REASON_POLICY_MISSING_EXIT_PLAN = "policy_missing_exit_plan"
REASON_POLICY_UNDEFINED_RISK = "policy_undefined_risk"


def validate_pre_trade_invariants(
    candidate: StrategyCandidate,
    *,
    settings: BotSettings,
    mode: str,
) -> RiskDecision:
    """Final hard-rule audit before a paper order can be created."""

    reasons: list[str] = []
    total_max_loss = candidate.total_max_loss()

    if mode != "paper":
        reasons.append(REASON_POLICY_NOT_PAPER_ONLY)

    if not settings.forbidden.allow_0dte and candidate.dte < settings.dte.forbidden_dte_min:
        reasons.append(REASON_POLICY_0DTE_FORBIDDEN)

    if (
        not settings.forbidden.allow_market_orders_options
        and candidate.order_type == OrderType.MARKET
    ):
        reasons.append(REASON_POLICY_MARKET_ORDER_FORBIDDEN)

    if total_max_loss is None:
        reasons.append(REASON_POLICY_MISSING_MAX_LOSS)
    elif total_max_loss <= 0:
        reasons.append(REASON_POLICY_INVALID_MAX_LOSS)

    if candidate.exit_plan is None or not candidate.exit_plan.is_defined():
        reasons.append(REASON_POLICY_MISSING_EXIT_PLAN)

    if _has_short_option_without_long_protection(candidate):
        reasons.append(REASON_POLICY_UNDEFINED_RISK)

    if reasons:
        return RiskDecision(
            approved=False,
            reason_codes=tuple(dict.fromkeys(reasons)),
            max_loss=total_max_loss,
            adjusted_size=None,
        )
    return RiskDecision(
        approved=True,
        reason_codes=(REASON_APPROVED,),
        max_loss=total_max_loss,
        adjusted_size=candidate.quantity,
    )


def _has_short_option_without_long_protection(candidate: StrategyCandidate) -> bool:
    short_legs = [leg for leg in candidate.legs if leg.action == OptionAction.SELL]
    long_legs = [leg for leg in candidate.legs if leg.action == OptionAction.BUY]
    if not short_legs:
        return False
    for short_leg in short_legs:
        protected = any(
            long_leg.contract.option_type == short_leg.contract.option_type
            and long_leg.contract.underlying.upper()
            == short_leg.contract.underlying.upper()
            and long_leg.contract.expiration >= short_leg.contract.expiration
            for long_leg in long_legs
        )
        if not protected:
            return True
    return False
