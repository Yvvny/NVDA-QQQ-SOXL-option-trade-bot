from __future__ import annotations

from trading_bot.config.settings import BotSettings
from trading_bot.core.enums import OptionAction
from trading_bot.core.models import RiskDecision, StrategyCandidate
from trading_bot.risk.engine import REASON_APPROVED

REASON_LIQUIDITY_MISSING_VOLUME = "liquidity_missing_volume"
REASON_LIQUIDITY_MISSING_OI = "liquidity_missing_open_interest"
REASON_LIQUIDITY_LOW_VOLUME = "liquidity_low_volume"
REASON_LIQUIDITY_LOW_OI = "liquidity_low_open_interest"
REASON_LIQUIDITY_WIDE_LEG_MARKET = "liquidity_wide_leg_market"
REASON_LIQUIDITY_WIDE_PACKAGE_MARKET = "liquidity_wide_package_market"
REASON_LIQUIDITY_INVALID_BID_ASK = "liquidity_invalid_bid_ask"
REASON_LIQUIDITY_MISSING_MID = "liquidity_missing_mid"
REASON_LIQUIDITY_DATA_MISSING_OBSERVATION_ONLY = (
    "liquidity_data_missing_observation_only"
)


def validate_candidate_liquidity(
    candidate: StrategyCandidate,
    settings: BotSettings,
) -> RiskDecision:
    reasons: list[str] = []
    missing_activity = False

    for leg in candidate.legs:
        contract = leg.contract
        mid = contract.effective_mid()
        if contract.bid is None or contract.ask is None:
            reasons.append(REASON_LIQUIDITY_INVALID_BID_ASK)
            continue
        if contract.bid <= 0 or contract.ask <= contract.bid:
            reasons.append(REASON_LIQUIDITY_INVALID_BID_ASK)
        if mid is None or mid <= 0:
            reasons.append(REASON_LIQUIDITY_MISSING_MID)
            continue

        width = contract.ask - contract.bid
        if width > max(
            settings.liquidity.max_abs_bid_ask_width,
            mid * settings.liquidity.max_bid_ask_pct_of_mid,
        ):
            reasons.append(REASON_LIQUIDITY_WIDE_LEG_MARKET)

        if contract.volume is None:
            missing_activity = True
            reasons.append(REASON_LIQUIDITY_MISSING_VOLUME)
        elif contract.volume < settings.liquidity.min_volume:
            reasons.append(REASON_LIQUIDITY_LOW_VOLUME)

        if contract.open_interest is None:
            missing_activity = True
            reasons.append(REASON_LIQUIDITY_MISSING_OI)
        elif contract.open_interest < settings.liquidity.min_open_interest:
            reasons.append(REASON_LIQUIDITY_LOW_OI)

    package_width = _package_width(candidate)
    package_entry = abs(_package_mid_entry(candidate))
    if (
        package_entry <= 0
        or package_width / package_entry > settings.liquidity.max_package_bid_ask_pct_of_entry
    ):
        reasons.append(REASON_LIQUIDITY_WIDE_PACKAGE_MARKET)

    observation_only = (
        missing_activity
        and settings.liquidity.paper_liquidity_observation_mode
        and candidate.quantity <= settings.liquidity.observation_max_contracts
    )
    if observation_only:
        reasons = [
            reason
            for reason in reasons
            if reason
            not in {
                REASON_LIQUIDITY_MISSING_VOLUME,
                REASON_LIQUIDITY_MISSING_OI,
            }
        ]

    if reasons:
        return RiskDecision(
            approved=False,
            reason_codes=_dedupe(reasons),
            max_loss=candidate.total_max_loss(),
            adjusted_size=None,
        )

    reason_codes = (REASON_APPROVED,)
    if observation_only:
        reason_codes = (REASON_APPROVED, REASON_LIQUIDITY_DATA_MISSING_OBSERVATION_ONLY)
    return RiskDecision(
        approved=True,
        reason_codes=reason_codes,
        max_loss=candidate.total_max_loss(),
        adjusted_size=candidate.quantity,
    )


def _package_mid_entry(candidate: StrategyCandidate) -> float:
    entry = 0.0
    for leg in candidate.legs:
        mid = leg.contract.effective_mid()
        if mid is None:
            return 0.0
        quantity = candidate.effective_leg_quantity(leg)
        if leg.action == OptionAction.SELL:
            entry += mid * quantity
        else:
            entry -= mid * quantity
    return entry


def _package_width(candidate: StrategyCandidate) -> float:
    width = 0.0
    for leg in candidate.legs:
        if leg.contract.bid is None or leg.contract.ask is None:
            return float("inf")
        width += (leg.contract.ask - leg.contract.bid) * candidate.effective_leg_quantity(leg)
    return width


def _dedupe(reason_codes: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for reason_code in reason_codes:
        if reason_code in seen:
            continue
        seen.add(reason_code)
        deduped.append(reason_code)
    return tuple(deduped)
