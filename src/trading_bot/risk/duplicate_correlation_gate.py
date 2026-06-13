from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from trading_bot.config.settings import BotSettings
from trading_bot.core.models import RiskDecision, StrategyCandidate
from trading_bot.core.time_utils import now_new_york, parse_timestamp
from trading_bot.risk.engine import REASON_APPROVED

REASON_DUPLICATE_EXACT_POSITION = "duplicate_exact_position"
REASON_DUPLICATE_NEAR_POSITION = "duplicate_near_position"
REASON_MAX_SYMBOL_DIRECTION_EXPOSURE = "max_symbol_direction_exposure"
REASON_MAX_THESIS_BUCKET_EXPOSURE = "max_thesis_bucket_exposure"
REASON_MAX_CORRELATED_TECH_BETA_EXPOSURE = "max_correlated_tech_beta_exposure"
REASON_RECENT_STOPOUT_COOLDOWN = "recent_stopout_cooldown"

TECH_BETA_SYMBOLS = frozenset({"QQQ", "NVDA", "SOXL", "SMH", "SOXX"})


@dataclass(frozen=True)
class GateLeg:
    action: str
    option_type: str
    strike: float
    expiration: str


@dataclass(frozen=True)
class GatePosition:
    symbol: str
    strategy_name: str
    max_loss: float
    legs: tuple[GateLeg, ...]
    opened_at: str | None = None
    closed_at: str | None = None
    exit_reason: str | None = None


def evaluate_duplicate_correlation_gate(
    candidate: StrategyCandidate,
    *,
    open_positions: tuple[GatePosition, ...],
    closed_positions: tuple[GatePosition, ...],
    account_equity: float,
    settings: BotSettings,
    preservation_mode_active: bool,
    checked_at: datetime | None = None,
) -> RiskDecision:
    total_max_loss = candidate.total_max_loss()
    if not settings.duplicate_correlation.enabled:
        return _approved(total_max_loss, candidate.quantity)

    reasons: list[str] = []
    candidate_position = _position_from_candidate(candidate)
    candidate_direction = _direction(candidate.strategy_name)
    candidate_thesis = _thesis_bucket(candidate_position.symbol, candidate_direction)

    for position in open_positions:
        if settings.duplicate_correlation.exact_duplicate_reject and _exact_duplicate(
            candidate_position,
            position,
        ):
            reasons.append(REASON_DUPLICATE_EXACT_POSITION)
        if settings.duplicate_correlation.near_duplicate_reject and _near_duplicate(
            candidate_position,
            position,
            settings,
        ):
            reasons.append(REASON_DUPLICATE_NEAR_POSITION)

    same_symbol_direction_count = sum(
        1
        for position in open_positions
        if position.symbol.upper() == candidate_position.symbol
        and _direction(position.strategy_name) == candidate_direction
    )
    if same_symbol_direction_count >= (
        settings.duplicate_correlation.max_positions_per_symbol_direction
    ):
        reasons.append(REASON_MAX_SYMBOL_DIRECTION_EXPOSURE)

    same_thesis_count = sum(
        1
        for position in open_positions
        if _thesis_bucket(position.symbol, _direction(position.strategy_name)) == candidate_thesis
    )
    if same_thesis_count >= settings.duplicate_correlation.max_positions_per_thesis_bucket:
        reasons.append(REASON_MAX_THESIS_BUCKET_EXPOSURE)

    if candidate_thesis == "BULL_TECH_BETA":
        bullish_tech_count = _tech_beta_count(open_positions, "bullish")
        if bullish_tech_count >= settings.duplicate_correlation.max_bullish_tech_beta_positions:
            reasons.append(REASON_MAX_CORRELATED_TECH_BETA_EXPOSURE)
    if candidate_thesis == "BEAR_TECH_BETA":
        bearish_tech_count = _tech_beta_count(open_positions, "bearish")
        if bearish_tech_count >= settings.duplicate_correlation.max_bearish_tech_beta_positions:
            reasons.append(REASON_MAX_CORRELATED_TECH_BETA_EXPOSURE)

    if preservation_mode_active and account_equity > 0:
        open_loss_pct = sum(position.max_loss for position in open_positions) / account_equity
        if (
            open_loss_pct
            >= settings.duplicate_correlation.preservation_block_open_max_loss_pct
            and candidate_thesis in {"BULL_TECH_BETA", "BEAR_TECH_BETA"}
        ):
            reasons.append(REASON_MAX_CORRELATED_TECH_BETA_EXPOSURE)

    if _recent_stopout(candidate_position, closed_positions, settings, checked_at):
        reasons.append(REASON_RECENT_STOPOUT_COOLDOWN)

    if reasons:
        return RiskDecision(
            approved=False,
            reason_codes=_dedupe(reasons),
            max_loss=total_max_loss,
            adjusted_size=None,
        )
    return _approved(total_max_loss, candidate.quantity)


def _approved(total_max_loss: float | None, quantity: int) -> RiskDecision:
    return RiskDecision(
        approved=True,
        reason_codes=(REASON_APPROVED,),
        max_loss=total_max_loss,
        adjusted_size=quantity,
    )


def _position_from_candidate(candidate: StrategyCandidate) -> GatePosition:
    return GatePosition(
        symbol=candidate.underlying.upper(),
        strategy_name=candidate.strategy_name,
        max_loss=candidate.total_max_loss() or 0.0,
        legs=tuple(
            GateLeg(
                action=leg.action.value,
                option_type=leg.contract.option_type.value,
                strike=leg.contract.strike,
                expiration=leg.contract.expiration.isoformat(),
            )
            for leg in candidate.legs
        ),
    )


def _direction(strategy_name: str) -> str:
    normalized = strategy_name.lower()
    if normalized in {"put_credit_spread", "call_debit_spread"}:
        return "bullish"
    if normalized in {"call_credit_spread", "put_debit_spread"}:
        return "bearish"
    return "neutral"


def _thesis_bucket(symbol: str, direction: str) -> str:
    normalized = symbol.upper()
    if normalized in TECH_BETA_SYMBOLS and direction == "bullish":
        return "BULL_TECH_BETA"
    if normalized in TECH_BETA_SYMBOLS and direction == "bearish":
        return "BEAR_TECH_BETA"
    return f"{direction.upper()}_{normalized}"


def _exact_duplicate(candidate: GatePosition, position: GatePosition) -> bool:
    return (
        candidate.symbol == position.symbol.upper()
        and candidate.strategy_name == position.strategy_name
        and _direction(candidate.strategy_name) == _direction(position.strategy_name)
        and _leg_key(candidate.legs) == _leg_key(position.legs)
    )


def _near_duplicate(
    candidate: GatePosition,
    position: GatePosition,
    settings: BotSettings,
) -> bool:
    if candidate.symbol != position.symbol.upper():
        return False
    if _direction(candidate.strategy_name) != _direction(position.strategy_name):
        return False
    if _strategy_family(candidate.strategy_name) != _strategy_family(position.strategy_name):
        return False
    if not _expiry_within_days(candidate.legs, position.legs, settings):
        return False
    if _strike_overlap_pct(candidate.legs, position.legs) < (
        settings.duplicate_correlation.near_duplicate_min_strike_overlap_pct
    ):
        return False
    if not _max_loss_similar(candidate.max_loss, position.max_loss, settings):
        return False
    return True


def _strategy_family(strategy_name: str) -> str:
    if "debit_spread" in strategy_name:
        return "debit_spread"
    if "credit_spread" in strategy_name:
        return "credit_spread"
    return strategy_name


def _leg_key(legs: tuple[GateLeg, ...]) -> tuple[tuple[str, str, float, str], ...]:
    return tuple(
        sorted((leg.action, leg.option_type, leg.strike, leg.expiration) for leg in legs)
    )


def _expiry_within_days(
    candidate_legs: tuple[GateLeg, ...],
    position_legs: tuple[GateLeg, ...],
    settings: BotSettings,
) -> bool:
    candidate_expiration = _first_expiration(candidate_legs)
    position_expiration = _first_expiration(position_legs)
    if candidate_expiration is None or position_expiration is None:
        return False
    return abs((candidate_expiration - position_expiration).days) <= (
        settings.duplicate_correlation.near_duplicate_expiry_days
    )


def _first_expiration(legs: tuple[GateLeg, ...]):
    if not legs:
        return None
    return datetime.fromisoformat(legs[0].expiration).date()


def _strike_overlap_pct(
    candidate_legs: tuple[GateLeg, ...],
    position_legs: tuple[GateLeg, ...],
) -> float:
    candidate_range = _strike_range(candidate_legs)
    position_range = _strike_range(position_legs)
    if candidate_range is None or position_range is None:
        return 0.0
    candidate_low, candidate_high = candidate_range
    position_low, position_high = position_range
    overlap = max(0.0, min(candidate_high, position_high) - max(candidate_low, position_low))
    smaller_width = max(0.01, min(candidate_high - candidate_low, position_high - position_low))
    return overlap / smaller_width


def _strike_range(legs: tuple[GateLeg, ...]) -> tuple[float, float] | None:
    if not legs:
        return None
    strikes = [leg.strike for leg in legs]
    return min(strikes), max(strikes)


def _max_loss_similar(
    candidate_max_loss: float,
    position_max_loss: float,
    settings: BotSettings,
) -> bool:
    denominator = max(candidate_max_loss, position_max_loss, 0.01)
    return abs(candidate_max_loss - position_max_loss) / denominator <= (
        settings.duplicate_correlation.near_duplicate_max_loss_similarity_pct
    )


def _tech_beta_count(open_positions: tuple[GatePosition, ...], direction: str) -> int:
    return sum(
        1
        for position in open_positions
        if position.symbol.upper() in TECH_BETA_SYMBOLS
        and _direction(position.strategy_name) == direction
    )


def _recent_stopout(
    candidate: GatePosition,
    closed_positions: tuple[GatePosition, ...],
    settings: BotSettings,
    checked_at: datetime | None,
) -> bool:
    now = checked_at or now_new_york()
    same_symbol_deadline = now - timedelta(
        days=settings.duplicate_correlation.stopout_cooldown_days_same_symbol_strategy
    )
    thesis_deadline = now - timedelta(
        days=settings.duplicate_correlation.stopout_cooldown_days_same_thesis_after_two_stopouts
    )
    candidate_direction = _direction(candidate.strategy_name)
    candidate_thesis = _thesis_bucket(candidate.symbol, candidate_direction)
    thesis_stopouts = 0
    for position in closed_positions:
        if "stop" not in str(position.exit_reason or "").lower():
            continue
        closed_at = parse_timestamp(position.closed_at)
        if closed_at is None:
            continue
        if (
            position.symbol.upper() == candidate.symbol
            and position.strategy_name == candidate.strategy_name
            and closed_at >= same_symbol_deadline
        ):
            return True
        if (
            _thesis_bucket(position.symbol, _direction(position.strategy_name))
            == candidate_thesis
            and closed_at >= thesis_deadline
        ):
            thesis_stopouts += 1
    return thesis_stopouts >= 2


def _dedupe(reason_codes: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for reason_code in reason_codes:
        if reason_code in seen:
            continue
        seen.add(reason_code)
        deduped.append(reason_code)
    return tuple(deduped)
