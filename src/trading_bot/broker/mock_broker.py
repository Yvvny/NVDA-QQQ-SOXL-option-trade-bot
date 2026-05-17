from __future__ import annotations

from itertools import count
from typing import TYPE_CHECKING

from trading_bot.broker.base import BrokerResult, LiveTradingDisabledError

if TYPE_CHECKING:
    from trading_bot.execution.order_builder import OptionOrder

_ORDER_COUNTER = count(1)


class MockBroker:
    def dry_run(self, order: OptionOrder) -> BrokerResult:
        order_number = next(_ORDER_COUNTER)
        return BrokerResult(
            accepted=True,
            order_id=f"dryrun-{order_number:06d}",
            message="Mock dry-run accepted. No live order was submitted.",
        )

    def submit(self, order: OptionOrder) -> BrokerResult:
        raise LiveTradingDisabledError("Live order submission is disabled in this early version.")
