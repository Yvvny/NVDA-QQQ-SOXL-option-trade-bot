from __future__ import annotations

from dataclasses import dataclass, field

from trading_bot.risk.kill_switch import KillSwitchState


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    strategy_name: str
    max_loss: float


@dataclass(frozen=True)
class PortfolioState:
    account_equity: float
    open_positions: tuple[OpenPosition, ...] = field(default_factory=tuple)
    daily_realized_pnl: float = 0.0
    weekly_realized_pnl: float = 0.0
    consecutive_losses: int = 0
    new_trades_today: int = 0
    new_trades_this_week: int = 0
    kill_switch: KillSwitchState = field(default_factory=KillSwitchState)

    @property
    def total_open_max_loss(self) -> float:
        return sum(position.max_loss for position in self.open_positions)

    def open_symbol_count(self, symbol: str) -> int:
        normalized = symbol.upper()
        return sum(1 for position in self.open_positions if position.symbol.upper() == normalized)

    def open_strategy_count(self, strategy_name: str) -> int:
        normalized = strategy_name.lower()
        return sum(
            1 for position in self.open_positions if position.strategy_name.lower() == normalized
        )
