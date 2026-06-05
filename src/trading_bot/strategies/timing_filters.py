from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from trading_bot.config.settings import BotSettings
from trading_bot.core.models import Candle
from trading_bot.core.time_utils import NEW_YORK_TIME_ZONE

DEBIT_SPREAD_STRATEGIES = frozenset({"call_debit_spread", "put_debit_spread"})
PRICE_ACTION_CONFIRMED_REASON = "price_action_confirmed"


@dataclass(frozen=True)
class EntryTimingContext:
    timestamp: datetime | None = None
    underlying_price: float | None = None
    vwap: float | None = None
    atr: float | None = None
    recent_candles: tuple[Candle, ...] = ()


@dataclass(frozen=True)
class EntryTimingDecision:
    approved: bool
    reason_codes: tuple[str, ...]


def evaluate_entry_timing(
    *,
    strategy_name: str,
    score_reason_codes: tuple[str, ...],
    context: EntryTimingContext | None,
    settings: BotSettings,
) -> EntryTimingDecision:
    if strategy_name not in DEBIT_SPREAD_STRATEGIES:
        return EntryTimingDecision(approved=True, reason_codes=("timing_not_required",))

    reason_codes: list[str] = []

    if (
        settings.strategy.debit_spread_require_price_action_confirmation
        and PRICE_ACTION_CONFIRMED_REASON not in score_reason_codes
    ):
        reason_codes.append("timing_debit_requires_price_action_confirmed")
    else:
        reason_codes.append("timing_price_action_confirmed")

    if context is None:
        reason_codes.append("timing_context_missing")
        return EntryTimingDecision(
            approved=not _has_blocking_reason(reason_codes),
            reason_codes=tuple(reason_codes),
        )

    if _in_opening_cooldown(
        context.timestamp,
        cooldown_minutes=settings.strategy.debit_spread_opening_cooldown_minutes,
    ):
        reason_codes.append("timing_opening_cooldown")
    elif context.timestamp is not None:
        reason_codes.append("timing_opening_cooldown_clear")

    reason_codes.extend(
        _anti_chase_price_reasons(
            strategy_name=strategy_name,
            context=context,
            warning_atr_multiple=settings.strategy.debit_spread_anti_chase_atr_multiple,
            hard_atr_multiple=settings.strategy.debit_spread_anti_chase_hard_atr_multiple,
        )
    )
    reason_codes.extend(
        _anti_chase_candle_reasons(
            strategy_name=strategy_name,
            candles=context.recent_candles,
            atr=context.atr,
            candle_count=settings.strategy.debit_spread_anti_chase_candle_count,
            strong_body_pct=settings.strategy.debit_spread_strong_candle_body_pct,
            min_body_atr_multiple=(
                settings.strategy.debit_spread_strong_candle_min_body_atr_multiple
            ),
        )
    )

    return EntryTimingDecision(
        approved=not _has_blocking_reason(reason_codes),
        reason_codes=tuple(reason_codes),
    )


def _in_opening_cooldown(timestamp: datetime | None, *, cooldown_minutes: int) -> bool:
    if timestamp is None or cooldown_minutes <= 0:
        return False

    minutes_since_open = _minutes_since_regular_open(timestamp)
    return minutes_since_open is not None and 0 <= minutes_since_open < cooldown_minutes


def _minutes_since_regular_open(timestamp: datetime) -> float | None:
    ny_timestamp = _to_new_york(timestamp)
    market_open = datetime.combine(
        ny_timestamp.date(),
        time(9, 30),
        tzinfo=NEW_YORK_TIME_ZONE,
    )
    return (ny_timestamp - market_open) / timedelta(minutes=1)


def _to_new_york(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=NEW_YORK_TIME_ZONE)
    return timestamp.astimezone(NEW_YORK_TIME_ZONE)


def _anti_chase_price_reasons(
    *,
    strategy_name: str,
    context: EntryTimingContext,
    warning_atr_multiple: float,
    hard_atr_multiple: float,
) -> tuple[str, ...]:
    price = context.underlying_price
    vwap = context.vwap
    atr = context.atr
    if price is None or vwap is None or atr is None or atr <= 0:
        return ("timing_anti_chase_price_data_missing",)

    hard_threshold = atr * hard_atr_multiple
    warning_threshold = atr * warning_atr_multiple
    if strategy_name == "call_debit_spread" and price > vwap + hard_threshold:
        return ("timing_call_debit_chasing_above_vwap_atr",)
    if strategy_name == "put_debit_spread" and price < vwap - hard_threshold:
        return ("timing_put_debit_chasing_below_vwap_atr",)
    if strategy_name == "call_debit_spread" and price > vwap + warning_threshold:
        return ("timing_call_debit_extended_above_vwap_atr_warning",)
    if strategy_name == "put_debit_spread" and price < vwap - warning_threshold:
        return ("timing_put_debit_extended_below_vwap_atr_warning",)
    return ("timing_anti_chase_price_clear",)


def _anti_chase_candle_reasons(
    *,
    strategy_name: str,
    candles: tuple[Candle, ...],
    atr: float | None,
    candle_count: int,
    strong_body_pct: float,
    min_body_atr_multiple: float,
) -> tuple[str, ...]:
    if candle_count <= 0:
        return ()
    if len(candles) < candle_count:
        return ("timing_anti_chase_candle_data_missing",)

    recent = candles[-candle_count:]
    if strategy_name == "call_debit_spread" and all(
        _is_strong_bullish_candle(candle, strong_body_pct, atr, min_body_atr_multiple)
        for candle in recent
    ):
        return ("timing_call_debit_after_three_strong_bullish_candles",)
    if strategy_name == "put_debit_spread" and all(
        _is_strong_bearish_candle(candle, strong_body_pct, atr, min_body_atr_multiple)
        for candle in recent
    ):
        return ("timing_put_debit_after_three_strong_bearish_candles",)
    return ("timing_anti_chase_candles_clear",)


def _is_strong_bullish_candle(
    candle: Candle,
    strong_body_pct: float,
    atr: float | None,
    min_body_atr_multiple: float,
) -> bool:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return False
    body = candle.close - candle.open
    return body > 0 and _body_is_strong(
        body,
        candle_range,
        strong_body_pct,
        atr,
        min_body_atr_multiple,
    )


def _is_strong_bearish_candle(
    candle: Candle,
    strong_body_pct: float,
    atr: float | None,
    min_body_atr_multiple: float,
) -> bool:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return False
    body = candle.open - candle.close
    return body > 0 and _body_is_strong(
        body,
        candle_range,
        strong_body_pct,
        atr,
        min_body_atr_multiple,
    )


def _body_is_strong(
    body: float,
    candle_range: float,
    strong_body_pct: float,
    atr: float | None,
    min_body_atr_multiple: float,
) -> bool:
    if atr is not None and atr > 0 and min_body_atr_multiple > 0:
        return body >= atr * min_body_atr_multiple
    return body / candle_range >= strong_body_pct


def _has_blocking_reason(reason_codes: list[str]) -> bool:
    return any(
        reason_code.startswith("timing_") and reason_code not in _CLEAR_REASONS
        for reason_code in reason_codes
    )


_CLEAR_REASONS = frozenset(
    {
        "timing_not_required",
        "timing_price_action_confirmed",
        "timing_context_missing",
        "timing_opening_cooldown_clear",
        "timing_anti_chase_price_data_missing",
        "timing_anti_chase_price_clear",
        "timing_call_debit_extended_above_vwap_atr_warning",
        "timing_put_debit_extended_below_vwap_atr_warning",
        "timing_anti_chase_candle_data_missing",
        "timing_anti_chase_candles_clear",
    }
)
