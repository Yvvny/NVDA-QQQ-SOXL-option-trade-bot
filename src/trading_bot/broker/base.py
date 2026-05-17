from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from trading_bot.execution.order_builder import OptionOrder


class LiveTradingDisabledError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrokerResult:
    accepted: bool
    order_id: str | None
    message: str


class BrokerAdapter(Protocol):
    def dry_run(self, order: OptionOrder) -> BrokerResult: ...

    def submit(self, order: OptionOrder) -> BrokerResult: ...
