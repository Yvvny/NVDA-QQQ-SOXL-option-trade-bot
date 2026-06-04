from __future__ import annotations

from dataclasses import dataclass

from trading_bot.config.settings import BotSettings, load_settings
from trading_bot.core.enums import OptionAction, OptionType, OrderType
from trading_bot.core.models import OptionLeg, StrategyCandidate


@dataclass(frozen=True)
class OrderLeg:
    symbol: str
    action: OptionAction
    quantity: int
    option_type: OptionType
    strike: float
    expiration: str


@dataclass(frozen=True)
class OptionOrder:
    underlying: str
    strategy_name: str
    order_type: OrderType
    price_effect: str
    limit_price: float
    legs: tuple[OrderLeg, ...]
    max_profit: float | None
    max_loss: float
    entry_score: float


class OrderBuilder:
    def __init__(self, settings: BotSettings | None = None) -> None:
        self.settings = settings or load_settings()

    def build(self, candidate: StrategyCandidate) -> OptionOrder:
        total_max_loss = candidate.total_max_loss()
        total_max_profit = candidate.total_max_profit()
        if total_max_loss is None:
            raise ValueError("Cannot build order without max_loss.")
        if candidate.order_type != OrderType.LIMIT:
            raise ValueError("Only limit option orders are supported.")

        price_effect = _price_effect(candidate.strategy_name)
        expected_price = candidate.expected_credit_or_debit / 100
        if price_effect == "credit":
            limit_price = max(
                0.01,
                expected_price - self.settings.execution.credit_spread_limit_offset_from_mid,
            )
        else:
            limit_price = (
                expected_price + self.settings.execution.debit_spread_limit_offset_from_mid
            )

        return OptionOrder(
            underlying=candidate.underlying,
            strategy_name=candidate.strategy_name,
            order_type=OrderType.LIMIT,
            price_effect=price_effect,
            limit_price=round(limit_price, 2),
            legs=tuple(_build_order_leg(candidate, leg) for leg in candidate.legs),
            max_profit=total_max_profit,
            max_loss=total_max_loss,
            entry_score=candidate.entry_score,
        )


def _price_effect(strategy_name: str) -> str:
    if strategy_name in {"put_credit_spread", "call_credit_spread", "iron_condor"}:
        return "credit"
    return "debit"


def _build_order_leg(candidate: StrategyCandidate, leg: OptionLeg) -> OrderLeg:
    contract = leg.contract
    return OrderLeg(
        symbol=contract.symbol,
        action=leg.action,
        quantity=candidate.effective_leg_quantity(leg),
        option_type=contract.option_type,
        strike=contract.strike,
        expiration=contract.expiration.isoformat(),
    )
