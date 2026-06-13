from __future__ import annotations

from dataclasses import dataclass

from trading_bot.core.models import Candle, ScoreBreakdown
from trading_bot.regime.classifier import RegimeLabel
from trading_bot.strategies.timing_filters import EntryTimingContext

DEBIT_SPREAD_STRATEGIES = frozenset({"call_debit_spread", "put_debit_spread"})


@dataclass(frozen=True)
class StrategyScoreInput:
    strategy_name: str
    regime_label: RegimeLabel
    iv_rank: float | None = None
    iv_percentile: float | None = None
    implied_volatility: float | None = None
    realized_volatility: float | None = None
    bid_ask_pct_of_mid: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    price_above_ema20: bool | None = None
    price_above_vwap: bool | None = None
    breakout_or_pullback_confirmed: bool = False
    major_event_within_24h: bool = False
    event_risk_intentionally_priced: bool = False
    entry_timing: EntryTimingContext | None = None
    debit_spread_pa_lookback_candles: int = 5
    debit_spread_pa_min_body_atr_multiple: float = 0.25
    debit_spread_pa_vwap_reclaim_tolerance_atr_multiple: float = 0.20


@dataclass(frozen=True)
class StrategyScoreResult:
    breakdown: ScoreBreakdown
    reason_codes: tuple[str, ...]

    @property
    def total(self) -> float:
        return self.breakdown.total


def score_strategy_setup(inputs: StrategyScoreInput) -> StrategyScoreResult:
    reason_codes: list[str] = []
    regime_fit = _score_regime_fit(inputs.strategy_name, inputs.regime_label, reason_codes)
    volatility_edge = _score_volatility(inputs, reason_codes)
    liquidity_quality = _score_liquidity(inputs, reason_codes)
    price_action = _score_price_action(inputs, reason_codes)
    event_risk = _score_event_risk(inputs, reason_codes)

    return StrategyScoreResult(
        breakdown=ScoreBreakdown(
            regime_fit=regime_fit,
            volatility_edge=volatility_edge,
            liquidity_quality=liquidity_quality,
            price_action=price_action,
            event_risk=event_risk,
        ),
        reason_codes=tuple(reason_codes),
    )


def _score_regime_fit(
    strategy_name: str,
    regime_label: RegimeLabel,
    reason_codes: list[str],
) -> float:
    preferred = {
        RegimeLabel.BULL_TREND_LOW_MID_IV: {"put_credit_spread", "call_debit_spread"},
        RegimeLabel.BULL_TREND_HIGH_IV: {"put_credit_spread"},
        RegimeLabel.RANGE_HIGH_IV: {"iron_condor", "put_credit_spread", "call_credit_spread"},
        RegimeLabel.RANGE_LOW_IV: {"calendar_spread", "diagonal_spread"},
        RegimeLabel.BEAR_TREND_HIGH_IV: {"call_credit_spread", "put_debit_spread"},
        RegimeLabel.CRASH_RISK_OFF: {"put_debit_spread", "hedge_only"},
    }
    reduced = {
        RegimeLabel.BULL_TREND_HIGH_IV: {"call_debit_spread"},
        RegimeLabel.BEAR_TREND_HIGH_IV: {"put_credit_spread"},
        RegimeLabel.RANGE_LOW_IV: {"call_debit_spread", "put_debit_spread"},
    }
    short_premium_strategies = {
        "put_credit_spread",
        "call_credit_spread",
        "iron_condor",
    }
    if regime_label == RegimeLabel.UNKNOWN:
        reason_codes.append("regime_hard_block_unknown")
        return 0.0
    if regime_label == RegimeLabel.UNSTABLE_CHOP:
        reason_codes.append("regime_hard_block_unstable_chop")
        return 0.0
    if regime_label == RegimeLabel.CRASH_RISK_OFF and strategy_name in short_premium_strategies:
        reason_codes.append("short_premium_blocked_crash_risk_off")
        return 0.0

    if strategy_name in preferred.get(regime_label, set()):
        reason_codes.append("regime_fit_preferred")
        return 30.0
    if strategy_name in reduced.get(regime_label, set()):
        reason_codes.append("regime_fit_reduced")
        return 18.0

    reason_codes.append("regime_fit_poor")
    return 6.0


def _score_volatility(inputs: StrategyScoreInput, reason_codes: list[str]) -> float:
    short_premium = inputs.strategy_name in {
        "put_credit_spread",
        "call_credit_spread",
        "iron_condor",
    }
    debit_spread = inputs.strategy_name in {"call_debit_spread", "put_debit_spread"}
    iv_rank = inputs.iv_rank if inputs.iv_rank is not None else inputs.iv_percentile

    if short_premium:
        if iv_rank is None:
            reason_codes.append("volatility_missing")
            return 12.0
        if iv_rank >= 50:
            reason_codes.append("volatility_edge_high_iv")
            return 25.0
        if iv_rank >= 30:
            reason_codes.append("volatility_edge_mid_iv")
            return 19.0
        reason_codes.append("volatility_edge_low_iv")
        return 9.0

    if debit_spread:
        if iv_rank is None:
            reason_codes.append("volatility_missing")
            return 15.0
        if iv_rank <= 45:
            reason_codes.append("volatility_edge_debit_reasonable")
            return 23.0
        reason_codes.append("volatility_edge_debit_expensive")
        return 12.0

    if inputs.implied_volatility is not None and inputs.realized_volatility is not None:
        if inputs.implied_volatility > inputs.realized_volatility:
            reason_codes.append("volatility_edge_iv_over_rv")
            return 20.0

    reason_codes.append("volatility_edge_neutral")
    return 15.0


def _score_liquidity(inputs: StrategyScoreInput, reason_codes: list[str]) -> float:
    score = 20.0
    if inputs.bid_ask_pct_of_mid is None:
        reason_codes.append("liquidity_missing_spread")
        score -= 8.0
    elif inputs.bid_ask_pct_of_mid <= 0.10:
        reason_codes.append("liquidity_tight_spread")
    elif inputs.bid_ask_pct_of_mid <= 0.15:
        reason_codes.append("liquidity_acceptable_spread")
        score -= 4.0
    else:
        reason_codes.append("liquidity_wide_spread")
        score -= 12.0

    if inputs.volume is None or inputs.open_interest is None:
        reason_codes.append("liquidity_missing_activity")
        score -= 4.0
    elif inputs.volume < 10 or inputs.open_interest < 100:
        reason_codes.append("liquidity_low_activity")
        score -= 8.0
    else:
        reason_codes.append("liquidity_activity_ok")

    return max(0.0, score)


def _score_price_action(inputs: StrategyScoreInput, reason_codes: list[str]) -> float:
    score = 0.0
    bullish = inputs.strategy_name in {"put_credit_spread", "call_debit_spread"}
    bearish = inputs.strategy_name in {"call_credit_spread", "put_debit_spread"}

    if bullish and inputs.price_above_ema20:
        score += 6.0
        reason_codes.append("price_above_ema20")
    if bullish and inputs.price_above_vwap:
        score += 4.0
        reason_codes.append("price_above_vwap")
    if bearish and inputs.price_above_ema20 is False:
        score += 6.0
        reason_codes.append("price_below_ema20")
    if bearish and inputs.price_above_vwap is False:
        score += 4.0
        reason_codes.append("price_below_vwap")
    if inputs.strategy_name in DEBIT_SPREAD_STRATEGIES:
        confirmed, price_action_reasons = evaluate_debit_price_action_confirmation(
            strategy_name=inputs.strategy_name,
            context=inputs.entry_timing,
            lookback_candles=inputs.debit_spread_pa_lookback_candles,
            min_body_atr_multiple=inputs.debit_spread_pa_min_body_atr_multiple,
            vwap_reclaim_tolerance_atr_multiple=(
                inputs.debit_spread_pa_vwap_reclaim_tolerance_atr_multiple
            ),
        )
        reason_codes.extend(price_action_reasons)
        if confirmed:
            score += 5.0
            reason_codes.append("price_action_confirmed")
        else:
            reason_codes.append("price_action_unconfirmed")
    elif inputs.breakout_or_pullback_confirmed:
        score += 5.0
        reason_codes.append("price_action_confirmed")

    if score == 0.0:
        reason_codes.append("price_action_neutral")
        return 7.0

    return min(15.0, score)


def evaluate_debit_price_action_confirmation(
    *,
    strategy_name: str,
    context: EntryTimingContext | None,
    lookback_candles: int,
    min_body_atr_multiple: float,
    vwap_reclaim_tolerance_atr_multiple: float,
) -> tuple[bool, tuple[str, ...]]:
    if strategy_name not in DEBIT_SPREAD_STRATEGIES:
        return False, ()
    if context is None or context.vwap is None or context.atr is None or context.atr <= 0:
        return False, ("price_action_unconfirmed_insufficient_candles",)

    required_previous = max(1, lookback_candles)
    candles = context.recent_candles
    if len(candles) < required_previous + 1:
        return False, ("price_action_unconfirmed_insufficient_candles",)

    last = candles[-1]
    previous = candles[-(required_previous + 1) : -1]
    body = abs(last.close - last.open)
    if body < context.atr * min_body_atr_multiple:
        return False, ("price_action_unconfirmed_small_body",)

    if strategy_name == "call_debit_spread":
        return _evaluate_call_debit_price_action(
            last=last,
            previous=previous,
            vwap=context.vwap,
            atr=context.atr,
            tolerance_multiple=vwap_reclaim_tolerance_atr_multiple,
        )
    return _evaluate_put_debit_price_action(
        last=last,
        previous=previous,
        vwap=context.vwap,
        atr=context.atr,
        tolerance_multiple=vwap_reclaim_tolerance_atr_multiple,
    )


def _evaluate_call_debit_price_action(
    *,
    last: Candle,
    previous: tuple[Candle, ...],
    vwap: float,
    atr: float,
    tolerance_multiple: float,
) -> tuple[bool, tuple[str, ...]]:
    if last.close <= vwap or last.close <= last.open:
        return False, ("price_action_unconfirmed_wrong_vwap_side",)

    if last.close > max(candle.high for candle in previous):
        return True, ("price_action_call_breakout_confirmed",)

    tolerance = atr * tolerance_multiple
    if any(candle.low <= vwap + tolerance for candle in previous):
        return True, ("price_action_call_vwap_reclaim_confirmed",)

    return False, ("price_action_unconfirmed_no_breakout_or_reclaim",)


def _evaluate_put_debit_price_action(
    *,
    last: Candle,
    previous: tuple[Candle, ...],
    vwap: float,
    atr: float,
    tolerance_multiple: float,
) -> tuple[bool, tuple[str, ...]]:
    if last.close >= vwap or last.close >= last.open:
        return False, ("price_action_unconfirmed_wrong_vwap_side",)

    if last.close < min(candle.low for candle in previous):
        return True, ("price_action_put_breakdown_confirmed",)

    tolerance = atr * tolerance_multiple
    if any(candle.high >= vwap - tolerance for candle in previous):
        return True, ("price_action_put_vwap_rejection_confirmed",)

    return False, ("price_action_unconfirmed_no_breakout_or_reclaim",)


def _score_event_risk(inputs: StrategyScoreInput, reason_codes: list[str]) -> float:
    if not inputs.major_event_within_24h:
        reason_codes.append("event_risk_clear")
        return 10.0
    if inputs.event_risk_intentionally_priced:
        reason_codes.append("event_risk_intentional")
        return 5.0
    reason_codes.append("event_risk_penalty")
    return 0.0
