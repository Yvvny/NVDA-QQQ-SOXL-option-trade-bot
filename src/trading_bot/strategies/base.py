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
            if not contract_liquidity_warnings(contract, self.settings)
        ]


def bid_ask_pct_of_mid(contract: OptionContract) -> float | None:
    mid = contract.effective_mid()
    if mid is None or mid <= 0 or contract.bid is None or contract.ask is None:
        return None
    return (contract.ask - contract.bid) / mid


def contract_liquidity_warnings(
    contract: OptionContract,
    settings: BotSettings | None = None,
) -> tuple[str, ...]:
    settings = settings or load_settings()
    warnings: list[str] = []

    if contract.bid is None or contract.ask is None:
        warnings.append("missing_bid_ask")
    if contract.effective_mid() is None:
        warnings.append("missing_mid")

    spread_pct = bid_ask_pct_of_mid(contract)
    if spread_pct is None:
        warnings.append("missing_bid_ask_pct")
    elif spread_pct > settings.liquidity.max_bid_ask_pct_of_mid:
        warnings.append("wide_bid_ask_spread")

    if contract.volume is None or contract.volume < settings.liquidity.min_volume:
        warnings.append("low_or_missing_volume")
    if (
        contract.open_interest is None
        or contract.open_interest < settings.liquidity.min_open_interest
    ):
        warnings.append("low_or_missing_open_interest")

    if not settings.liquidity.allow_missing_greeks and contract.delta is None:
        warnings.append("missing_delta")

    return tuple(warnings)
