from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from trading_bot.core.models import Candle
from trading_bot.indicators.ema import ema


class RegimeLabel(StrEnum):
    BULL_TREND_LOW_MID_IV = "bull_trend_low_mid_iv"
    BULL_TREND_HIGH_IV = "bull_trend_high_iv"
    RANGE_HIGH_IV = "range_high_iv"
    RANGE_LOW_IV = "range_low_iv"
    BEAR_TREND_HIGH_IV = "bear_trend_high_iv"
    CRASH_RISK_OFF = "crash_risk_off"


@dataclass(frozen=True)
class MarketRegimeInput:
    qqq_close: float | None = None
    qqq_ema20: float | None = None
    qqq_ema50: float | None = None
    spy_close: float | None = None
    spy_ema20: float | None = None
    spy_ema50: float | None = None
    qqq_return_5d: float | None = None
    qqq_return_20d: float | None = None
    semiconductor_close: float | None = None
    semiconductor_ema20: float | None = None
    semiconductor_ema50: float | None = None
    vix_level: float | None = None
    vix_change_pct: float | None = None
    iv_rank: float | None = None
    iv_percentile: float | None = None
    realized_volatility: float | None = None
    implied_volatility: float | None = None
    major_event_within_24h: bool = False
    stale_or_inconsistent_data: bool = False


@dataclass(frozen=True)
class RegimeDecision:
    label: RegimeLabel
    confidence: float
    reason_codes: tuple[str, ...]
    preferred_strategies: tuple[str, ...] = field(default_factory=tuple)


class RegimeClassifier:
    def classify(self, inputs: MarketRegimeInput) -> RegimeDecision:
        reason_codes: list[str] = []
        confidence = 0.70

        missing_count = _missing_required_input_count(inputs)
        if missing_count:
            confidence -= min(0.35, missing_count * 0.04)
            reason_codes.append("degraded_missing_inputs")

        if inputs.stale_or_inconsistent_data:
            return RegimeDecision(
                label=RegimeLabel.CRASH_RISK_OFF,
                confidence=max(0.35, confidence),
                reason_codes=tuple([*reason_codes, "stale_or_inconsistent_data"]),
                preferred_strategies=("hedge_only",),
            )

        if _is_crash_risk(inputs):
            return RegimeDecision(
                label=RegimeLabel.CRASH_RISK_OFF,
                confidence=min(0.95, confidence + 0.20),
                reason_codes=tuple([*reason_codes, "crash_risk_conditions"]),
                preferred_strategies=("hedge_only", "put_debit_spread"),
            )

        bull_score = _trend_score(
            close=inputs.qqq_close,
            ema20=inputs.qqq_ema20,
            ema50=inputs.qqq_ema50,
            return_20d=inputs.qqq_return_20d,
        )
        spy_bull_score = _trend_score(
            close=inputs.spy_close,
            ema20=inputs.spy_ema20,
            ema50=inputs.spy_ema50,
            return_20d=None,
        )
        semi_bull_score = _trend_score(
            close=inputs.semiconductor_close,
            ema20=inputs.semiconductor_ema20,
            ema50=inputs.semiconductor_ema50,
            return_20d=None,
        )
        combined_bull_score = bull_score + (0.5 * spy_bull_score) + (0.5 * semi_bull_score)

        bear_score = _bear_score(inputs)
        iv_high = _is_iv_high(inputs)

        if combined_bull_score >= 3.0 and bear_score < 2.0:
            reason_codes.append("bull_trend_confirmed")
            return RegimeDecision(
                label=(
                    RegimeLabel.BULL_TREND_HIGH_IV if iv_high else RegimeLabel.BULL_TREND_LOW_MID_IV
                ),
                confidence=min(0.95, confidence + 0.15),
                reason_codes=tuple(reason_codes),
                preferred_strategies=("put_credit_spread", "call_debit_spread"),
            )

        if bear_score >= 2.5:
            reason_codes.append("bear_trend_confirmed")
            return RegimeDecision(
                label=RegimeLabel.BEAR_TREND_HIGH_IV,
                confidence=min(0.90, confidence + 0.10),
                reason_codes=tuple(reason_codes),
                preferred_strategies=("call_credit_spread", "put_debit_spread"),
            )

        reason_codes.append("range_conditions")
        return RegimeDecision(
            label=RegimeLabel.RANGE_HIGH_IV if iv_high else RegimeLabel.RANGE_LOW_IV,
            confidence=max(0.30, confidence),
            reason_codes=tuple(reason_codes),
            preferred_strategies=(
                ("iron_condor", "short_premium")
                if iv_high
                else ("calendar_spread", "diagonal_spread")
            ),
        )


def classify_from_daily_candles(
    qqq_candles: Sequence[Candle],
    spy_candles: Sequence[Candle] | None = None,
    semiconductor_candles: Sequence[Candle] | None = None,
    vix_level: float | None = None,
    iv_rank: float | None = None,
) -> RegimeDecision:
    classifier = RegimeClassifier()
    inputs = MarketRegimeInput(
        qqq_close=_last_close(qqq_candles),
        qqq_ema20=_last_indicator(ema(_closes(qqq_candles), 20)),
        qqq_ema50=_last_indicator(ema(_closes(qqq_candles), 50)),
        spy_close=_last_close(spy_candles or ()),
        spy_ema20=_last_indicator(ema(_closes(spy_candles or ()), 20)),
        spy_ema50=_last_indicator(ema(_closes(spy_candles or ()), 50)),
        qqq_return_5d=_return_over_period(qqq_candles, 5),
        qqq_return_20d=_return_over_period(qqq_candles, 20),
        semiconductor_close=_last_close(semiconductor_candles or ()),
        semiconductor_ema20=_last_indicator(ema(_closes(semiconductor_candles or ()), 20)),
        semiconductor_ema50=_last_indicator(ema(_closes(semiconductor_candles or ()), 50)),
        vix_level=vix_level,
        iv_rank=iv_rank,
    )
    return classifier.classify(inputs)


def _is_crash_risk(inputs: MarketRegimeInput) -> bool:
    if inputs.vix_level is not None and inputs.vix_level >= 35:
        return True
    if inputs.vix_change_pct is not None and inputs.vix_change_pct >= 0.25:
        return True
    if inputs.qqq_return_5d is not None and inputs.qqq_return_5d <= -0.08:
        return True
    return False


def _trend_score(
    close: float | None,
    ema20: float | None,
    ema50: float | None,
    return_20d: float | None,
) -> float:
    score = 0.0
    if close is not None and ema20 is not None and close > ema20:
        score += 1.0
    if close is not None and ema50 is not None and close > ema50:
        score += 1.0
    if ema20 is not None and ema50 is not None and ema20 > ema50:
        score += 1.0
    if return_20d is not None and return_20d > 0:
        score += 0.5
    return score


def _bear_score(inputs: MarketRegimeInput) -> float:
    score = 0.0
    if (
        inputs.qqq_close is not None
        and inputs.qqq_ema20 is not None
        and inputs.qqq_close < inputs.qqq_ema20
    ):
        score += 1.0
    if (
        inputs.qqq_close is not None
        and inputs.qqq_ema50 is not None
        and inputs.qqq_close < inputs.qqq_ema50
    ):
        score += 1.0
    if (
        inputs.qqq_ema20 is not None
        and inputs.qqq_ema50 is not None
        and inputs.qqq_ema20 < inputs.qqq_ema50
    ):
        score += 1.0
    if inputs.qqq_return_20d is not None and inputs.qqq_return_20d < 0:
        score += 0.5
    return score


def _is_iv_high(inputs: MarketRegimeInput) -> bool:
    if inputs.iv_rank is not None:
        return inputs.iv_rank >= 35
    if inputs.iv_percentile is not None:
        return inputs.iv_percentile >= 50
    if inputs.vix_level is not None:
        return inputs.vix_level >= 22
    if inputs.implied_volatility is not None and inputs.realized_volatility is not None:
        return inputs.implied_volatility > inputs.realized_volatility * 1.1
    return False


def _missing_required_input_count(inputs: MarketRegimeInput) -> int:
    values = (
        inputs.qqq_close,
        inputs.qqq_ema20,
        inputs.qqq_ema50,
        inputs.spy_close,
        inputs.spy_ema20,
        inputs.spy_ema50,
        inputs.qqq_return_5d,
        inputs.qqq_return_20d,
        inputs.vix_level,
    )
    return sum(1 for value in values if value is None)


def _closes(candles: Sequence[Candle]) -> list[float]:
    return [candle.close for candle in candles]


def _last_close(candles: Sequence[Candle]) -> float | None:
    return candles[-1].close if candles else None


def _last_indicator(values: Sequence[float | None]) -> float | None:
    for value in reversed(values):
        if value is not None:
            return value
    return None


def _return_over_period(candles: Sequence[Candle], period: int) -> float | None:
    if len(candles) <= period:
        return None
    start = candles[-period - 1].close
    end = candles[-1].close
    if start == 0:
        return None
    return (end - start) / start
