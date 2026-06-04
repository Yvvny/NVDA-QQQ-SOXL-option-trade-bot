from __future__ import annotations

from collections.abc import Sequence

from trading_bot.config.settings import BotSettings, load_settings
from trading_bot.core.models import OptionContract, StrategyCandidate
from trading_bot.risk.portfolio import PortfolioState

NON_BLOCKING_LIQUIDITY_WARNINGS = frozenset(
    {
        "missing_volume_metadata",
        "missing_open_interest_metadata",
    }
)


class StrategyEngine:
    def __init__(self, settings: BotSettings | None = None) -> None:
        self.settings = settings or load_settings()

    def _eligible_contracts(self, contracts: Sequence[OptionContract]) -> list[OptionContract]:
        return [
            contract
            for contract in contracts
            if not blocking_liquidity_warnings(contract, self.settings)
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

    if contract.volume is None:
        if contract.allow_missing_activity_data:
            warnings.append("missing_volume_metadata")
        else:
            warnings.append("low_or_missing_volume")
    elif contract.volume < settings.liquidity.min_volume:
        warnings.append("low_or_missing_volume")

    if contract.open_interest is None:
        if contract.allow_missing_activity_data:
            warnings.append("missing_open_interest_metadata")
        else:
            warnings.append("low_or_missing_open_interest")
    elif contract.open_interest < settings.liquidity.min_open_interest:
        warnings.append("low_or_missing_open_interest")

    if not settings.liquidity.allow_missing_greeks and contract.delta is None:
        warnings.append("missing_delta")

    return tuple(warnings)


def blocking_liquidity_warnings(
    contract: OptionContract,
    settings: BotSettings | None = None,
) -> tuple[str, ...]:
    return tuple(
        warning
        for warning in contract_liquidity_warnings(contract, settings)
        if warning not in NON_BLOCKING_LIQUIDITY_WARNINGS
    )


def candidate_quality_score(
    candidate: StrategyCandidate,
    risk_cap: float,
    portfolio_state: PortfolioState | None = None,
) -> tuple[float, ...]:
    reward_risk = _reward_risk_ratio(candidate)
    spread_quality = _spread_quality_score(candidate)
    diversification = _diversification_score(candidate, portfolio_state)
    risk_utilization = _risk_utilization_score(candidate, risk_cap)
    max_profit = candidate.max_profit or 0.0
    max_loss = candidate.max_loss or float("inf")
    return (
        reward_risk,
        spread_quality,
        diversification,
        candidate.entry_score / 100.0,
        risk_utilization,
        max_profit,
        -max_loss,
    )


def _reward_risk_ratio(candidate: StrategyCandidate) -> float:
    if candidate.max_profit is None or candidate.max_loss is None or candidate.max_loss <= 0:
        return 0.0
    return candidate.max_profit / candidate.max_loss


def _spread_quality_score(candidate: StrategyCandidate) -> float:
    spread_pcts = [
        spread_pct
        for leg in candidate.legs
        if (spread_pct := bid_ask_pct_of_mid(leg.contract)) is not None
    ]
    if not spread_pcts:
        return 0.0
    average_spread_pct = sum(spread_pcts) / len(spread_pcts)
    return max(0.0, 1.0 - average_spread_pct)


def _risk_utilization_score(candidate: StrategyCandidate, risk_cap: float) -> float:
    if candidate.max_loss is None or candidate.max_loss <= 0 or risk_cap <= 0:
        return 0.0
    utilization = min(candidate.max_loss / risk_cap, 1.5)
    return max(0.0, 1.0 - abs(utilization - 0.60))


def _diversification_score(
    candidate: StrategyCandidate,
    portfolio_state: PortfolioState | None,
) -> float:
    if portfolio_state is None:
        return 1.0

    symbol_penalty = 0.35 * portfolio_state.open_symbol_count(candidate.underlying)
    strategy_penalty = 0.15 * portfolio_state.open_strategy_count(candidate.strategy_name)
    total_penalty = min(symbol_penalty + strategy_penalty, 0.90)
    return max(0.10, 1.0 - total_penalty)
