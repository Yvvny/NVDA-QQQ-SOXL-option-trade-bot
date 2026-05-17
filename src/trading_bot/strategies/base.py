from __future__ import annotations

from collections.abc import Sequence

from trading_bot.config.settings import BotSettings, load_settings
from trading_bot.core.models import OptionContract


class StrategyEngine:
    def __init__(self, settings: BotSettings | None = None) -> None:
        self.settings = settings or load_settings()

    def _eligible_contracts(self, contracts: Sequence[OptionContract]) -> list[OptionContract]:
        return [
            contract
            for contract in contracts
            if contract.effective_mid() is not None
            and contract.bid is not None
            and contract.ask is not None
        ]


def bid_ask_pct_of_mid(contract: OptionContract) -> float | None:
    mid = contract.effective_mid()
    if mid is None or mid <= 0 or contract.bid is None or contract.ask is None:
        return None
    return (contract.ask - contract.bid) / mid
