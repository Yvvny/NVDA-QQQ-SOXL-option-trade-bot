from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from trading_bot.core.enums import OptionAction, OptionType, OrderType


@dataclass(frozen=True)
class UnderlyingQuote:
    symbol: str
    timestamp: datetime
    bid: float | None
    ask: float | None
    last: float
    volume: int | None = None


@dataclass(frozen=True)
class Candle:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    underlying: str
    expiration: date
    strike: float
    option_type: OptionType
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    iv: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    allow_missing_activity_data: bool = False

    def effective_mid(self) -> float | None:
        if self.mid is not None:
            return self.mid
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class OptionLeg:
    contract: OptionContract
    action: OptionAction
    quantity: int = 1

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("Option leg quantity must be positive.")


@dataclass(frozen=True)
class ExitPlan:
    profit_target_pct: float | None = None
    stop_loss_pct: float | None = None
    stop_loss_multiple: float | None = None
    time_exit_dte: int | None = None
    reason_codes: tuple[str, ...] = field(default_factory=tuple)

    def is_defined(self) -> bool:
        return any(
            value is not None
            for value in (
                self.profit_target_pct,
                self.stop_loss_pct,
                self.stop_loss_multiple,
                self.time_exit_dte,
            )
        ) or bool(self.reason_codes)


@dataclass(frozen=True)
class ScoreBreakdown:
    regime_fit: float = 0.0
    volatility_edge: float = 0.0
    liquidity_quality: float = 0.0
    price_action: float = 0.0
    event_risk: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.regime_fit
            + self.volatility_edge
            + self.liquidity_quality
            + self.price_action
            + self.event_risk
        )


@dataclass(frozen=True)
class StrategyCandidate:
    strategy_name: str
    underlying: str
    legs: tuple[OptionLeg, ...]
    dte: int
    entry_score: float
    max_profit: float | None
    max_loss: float | None
    expected_credit_or_debit: float
    reason_codes: tuple[str, ...]
    exit_plan: ExitPlan | None
    order_type: OrderType = OrderType.LIMIT
    quantity: int = 1
    score_breakdown: ScoreBreakdown | None = None
    event_risk_blocked: bool = False
    liquidity_ok: bool = True
    liquidity_warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("Candidate quantity must be positive.")

    def total_max_loss(self) -> float | None:
        if self.max_loss is None:
            return None
        return round(self.max_loss * self.quantity, 2)

    def total_max_profit(self) -> float | None:
        if self.max_profit is None:
            return None
        return round(self.max_profit * self.quantity, 2)

    def total_expected_credit_or_debit(self) -> float:
        return round(self.expected_credit_or_debit * self.quantity, 2)

    def effective_leg_quantity(self, leg: OptionLeg) -> int:
        return leg.quantity * self.quantity


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason_codes: tuple[str, ...]
    max_loss: float | None
    adjusted_size: int | None


@dataclass(frozen=True)
class TradeRecord:
    trade_id: str
    timestamp: datetime
    mode: Literal["research", "backtest", "paper", "dry_run", "live"]
    symbol: str
    strategy_name: str
    legs: tuple[OptionLeg, ...]
    entry_score: float
    risk_decision: RiskDecision
    status: str
