from __future__ import annotations

from dataclasses import dataclass, field

from trading_bot.config.settings import BotSettings, load_settings
from trading_bot.core.enums import OptionType
from trading_bot.core.models import StrategyCandidate
from trading_bot.regime.classifier import RegimeLabel
from trading_bot.strategies.base import bid_ask_pct_of_mid
from trading_bot.strategies.reward_risk import (
    NON_BLOCKING_REWARD_RISK_REASONS,
    planned_reward_risk_reasons,
)

STRICT_MIN_SCORE = 60.0
STRICT_MAX_BID_ASK_PCT_OF_MID = 0.12

STRICT_ALLOWED_STRATEGIES_BY_REGIME: dict[RegimeLabel, frozenset[str]] = {
    RegimeLabel.BULL_TREND_LOW_MID_IV: frozenset(
        {"put_credit_spread", "call_debit_spread", "diagonal_spread"}
    ),
    RegimeLabel.BULL_TREND_HIGH_IV: frozenset({"put_credit_spread"}),
    RegimeLabel.RANGE_HIGH_IV: frozenset(
        {"iron_condor", "put_credit_spread", "call_credit_spread"}
    ),
    RegimeLabel.RANGE_LOW_IV: frozenset({"calendar_spread", "diagonal_spread"}),
    RegimeLabel.BEAR_TREND_HIGH_IV: frozenset({"call_credit_spread", "put_debit_spread"}),
    RegimeLabel.CRASH_RISK_OFF: frozenset({"put_debit_spread"}),
}

PAPER_EXPERIMENTAL_ALLOWED_STRATEGIES_BY_REGIME: dict[RegimeLabel, frozenset[str]] = {
    RegimeLabel.RANGE_LOW_IV: frozenset({"call_debit_spread"}),
}


@dataclass(frozen=True)
class SpecComplianceDecision:
    approved: bool
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)


def validate_candidate_against_strategy_spec(
    candidate: StrategyCandidate,
    *,
    regime_label: RegimeLabel,
    risk_budget_base: float,
    experimental_mode: bool = False,
    settings: BotSettings | None = None,
) -> SpecComplianceDecision:
    settings = settings or load_settings()
    reasons: list[str] = []
    warnings: list[str] = []
    underlying = candidate.underlying.upper()
    strategy = candidate.strategy_name

    if candidate.entry_score < STRICT_MIN_SCORE:
        reasons.append("spec_score_below_60")

    allowed = set(STRICT_ALLOWED_STRATEGIES_BY_REGIME.get(regime_label, frozenset()))
    experimental_allowed = PAPER_EXPERIMENTAL_ALLOWED_STRATEGIES_BY_REGIME.get(
        regime_label,
        frozenset(),
    )
    used_experimental_override = False
    if experimental_mode and strategy in experimental_allowed:
        allowed.add(strategy)
        used_experimental_override = strategy not in STRICT_ALLOWED_STRATEGIES_BY_REGIME.get(
            regime_label,
            frozenset(),
        )
    if strategy not in allowed:
        reasons.append(f"spec_strategy_not_allowed_for_{regime_label.value}")
    elif used_experimental_override:
        warnings.append(f"spec_experimental_strategy_override_for_{regime_label.value}")

    if candidate.event_risk_blocked or "event_risk_penalty" in candidate.reason_codes:
        reasons.append("spec_major_event_risk_block")

    if "volatility_missing" in candidate.reason_codes:
        warnings.append("spec_iv_rank_or_iv_percentile_missing")
    if "price_action_neutral" in candidate.reason_codes and strategy in {
        "call_credit_spread",
        "call_debit_spread",
        "put_debit_spread",
    }:
        warnings.append("spec_price_action_confirmation_missing")

    _validate_underlying_rules(candidate, reasons)
    _validate_dte_rules(candidate, regime_label, reasons, warnings)
    _validate_delta_rules(candidate, settings, reasons)
    _validate_liquidity_rules(candidate, reasons, warnings)
    _validate_credit_or_debit_rules(candidate, settings, reasons, warnings)
    _validate_small_account_risk(candidate, underlying, risk_budget_base, settings, reasons)

    return SpecComplianceDecision(
        approved=not reasons,
        reason_codes=_dedupe(reasons),
        warnings=_dedupe(warnings),
    )


def _validate_underlying_rules(candidate: StrategyCandidate, reasons: list[str]) -> None:
    underlying = candidate.underlying.upper()
    strategy = candidate.strategy_name

    if strategy == "iron_condor" and underlying not in {"SPY", "QQQ", "IWM"}:
        reasons.append("spec_iron_condor_underlying_not_preferred")
    if strategy in {"calendar_spread", "diagonal_spread"} and underlying not in {
        "QQQ",
        "SPY",
        "NVDA",
    }:
        reasons.append("spec_calendar_diagonal_underlying_not_allowed")
    if underlying == "SOXL" and strategy in {
        "put_credit_spread",
        "call_credit_spread",
        "iron_condor",
    }:
        reasons.append("spec_soxl_short_premium_not_allowed_v1")


def _validate_dte_rules(
    candidate: StrategyCandidate,
    regime_label: RegimeLabel,
    reasons: list[str],
    warnings: list[str],
) -> None:
    strategy = candidate.strategy_name
    dte = candidate.dte
    underlying = candidate.underlying.upper()

    if strategy == "put_credit_spread":
        if regime_label == RegimeLabel.BULL_TREND_HIGH_IV and not 21 <= dte <= 35:
            reasons.append("spec_put_credit_high_iv_dte_out_of_range")
        elif regime_label != RegimeLabel.BULL_TREND_HIGH_IV and not 30 <= dte <= 60:
            warnings.append("spec_put_credit_ivr_bucket_dte_not_fully_verified")
    elif strategy == "call_credit_spread" and not 21 <= dte <= 45:
        reasons.append("spec_call_credit_dte_out_of_range")
    elif strategy == "iron_condor" and not 30 <= dte <= 45:
        reasons.append("spec_iron_condor_dte_out_of_range")
    elif strategy == "call_debit_spread":
        if underlying == "SOXL" and not 7 <= dte <= 21:
            reasons.append("spec_soxl_call_debit_dte_out_of_range")
        elif underlying != "SOXL" and not 14 <= dte <= 45:
            reasons.append("spec_call_debit_dte_out_of_range")
    elif strategy == "put_debit_spread" and not 14 <= dte <= 45:
        reasons.append("spec_put_debit_dte_out_of_range")
    elif strategy in {"calendar_spread", "diagonal_spread"} and not 7 <= dte <= 21:
        reasons.append("spec_calendar_front_dte_out_of_range")


def _validate_delta_rules(
    candidate: StrategyCandidate,
    settings: BotSettings,
    reasons: list[str],
) -> None:
    strategy = candidate.strategy_name
    legs = candidate.legs

    if any(leg.contract.delta is None for leg in legs):
        reasons.append("spec_missing_delta")
        return

    if strategy == "put_credit_spread":
        short_put = _short_leg_delta(candidate, OptionType.PUT)
        if (
            short_put is None
            or not settings.delta.short_premium_min_abs
            <= abs(short_put)
            <= settings.delta.short_premium_max_abs
        ):
            reasons.append("spec_put_credit_short_delta_out_of_range")
    elif strategy == "call_credit_spread":
        short_call = _short_leg_delta(candidate, OptionType.CALL)
        if (
            short_call is None
            or not settings.delta.short_premium_min_abs
            <= abs(short_call)
            <= settings.delta.short_premium_max_abs
        ):
            reasons.append("spec_call_credit_short_delta_out_of_range")
    elif strategy == "iron_condor":
        short_put = _short_leg_delta(candidate, OptionType.PUT)
        short_call = _short_leg_delta(candidate, OptionType.CALL)
        if short_put is None or not 0.16 <= abs(short_put) <= 0.25:
            reasons.append("spec_iron_condor_short_put_delta_out_of_range")
        if short_call is None or not 0.16 <= abs(short_call) <= 0.25:
            reasons.append("spec_iron_condor_short_call_delta_out_of_range")
    elif strategy in {"call_debit_spread", "put_debit_spread", "diagonal_spread"}:
        long_delta = _long_leg_primary_delta(candidate)
        short_delta = _short_leg_primary_delta(candidate)
        if long_delta is None or not 0.45 <= abs(long_delta) <= 0.65:
            reasons.append("spec_trend_long_delta_out_of_range")
        if short_delta is None or not 0.20 <= abs(short_delta) <= 0.40:
            reasons.append("spec_trend_short_delta_out_of_range")


def _validate_liquidity_rules(
    candidate: StrategyCandidate,
    reasons: list[str],
    warnings: list[str],
) -> None:
    for leg in candidate.legs:
        spread_pct = bid_ask_pct_of_mid(leg.contract)
        if spread_pct is None:
            reasons.append("spec_missing_bid_ask_spread")
        elif spread_pct > STRICT_MAX_BID_ASK_PCT_OF_MID:
            reasons.append("spec_bid_ask_spread_above_12pct_mid")
        if leg.contract.volume is None:
            if leg.contract.allow_missing_activity_data:
                warnings.append("spec_missing_volume_metadata")
            else:
                reasons.append("spec_low_or_missing_volume")
        elif leg.contract.volume < 10:
            reasons.append("spec_low_or_missing_volume")

        if leg.contract.open_interest is None:
            if leg.contract.allow_missing_activity_data:
                warnings.append("spec_missing_open_interest_metadata")
            else:
                reasons.append("spec_low_or_missing_open_interest")
        elif leg.contract.open_interest < 100:
            reasons.append("spec_low_or_missing_open_interest")


def _validate_credit_or_debit_rules(
    candidate: StrategyCandidate,
    settings: BotSettings,
    reasons: list[str],
    warnings: list[str],
) -> None:
    strategy = candidate.strategy_name
    if strategy in {"put_credit_spread", "call_credit_spread"}:
        width = _spread_width(candidate)
        credit = candidate.expected_credit_or_debit / 100
        if width is None or not (
            settings.strategy.credit_spread_min_pct_of_width
            <= credit / width
            <= settings.strategy.credit_spread_max_pct_of_width
        ):
            reasons.append("spec_credit_spread_credit_target_out_of_range")
    elif strategy == "iron_condor":
        width = _spread_width(candidate)
        credit = candidate.expected_credit_or_debit / 100
        if width is None or not 0.20 <= credit / width <= 0.35:
            reasons.append("spec_iron_condor_credit_target_out_of_range")
    elif strategy in {"call_debit_spread", "put_debit_spread"}:
        if candidate.max_profit is None or candidate.max_loss is None:
            reasons.append("spec_debit_spread_missing_reward_risk")
        elif candidate.max_profit / candidate.max_loss < 1.2:
            reasons.append("spec_debit_spread_reward_risk_below_1_2")

    for reason in planned_reward_risk_reasons(candidate, settings):
        if reason in NON_BLOCKING_REWARD_RISK_REASONS:
            warnings.append(f"spec_{reason}")
        else:
            reasons.append(f"spec_{reason}")


def _validate_small_account_risk(
    candidate: StrategyCandidate,
    underlying: str,
    risk_budget_base: float,
    settings: BotSettings,
    reasons: list[str],
) -> None:
    total_max_loss = candidate.total_max_loss()
    if total_max_loss is None:
        reasons.append("spec_missing_max_loss")
        return
    per_trade_limit = settings.risk.per_trade_max_loss_cap(
        risk_budget_base,
        candidate.entry_score,
    )
    if candidate.entry_score >= 80 and total_max_loss > per_trade_limit:
        reasons.append("spec_high_score_trade_risk_above_40pct_equity")
    if candidate.entry_score < 80 and total_max_loss > per_trade_limit:
        reasons.append("spec_normal_trade_risk_above_20pct_equity")
    if underlying == "SOXL" and total_max_loss > risk_budget_base * 0.10:
        reasons.append("spec_soxl_trade_risk_above_10pct")


def _short_leg_delta(candidate: StrategyCandidate, option_type: OptionType) -> float | None:
    for leg in candidate.legs:
        if leg.action.value == "sell" and leg.contract.option_type == option_type:
            return leg.contract.delta
    return None


def _long_leg_primary_delta(candidate: StrategyCandidate) -> float | None:
    for leg in candidate.legs:
        if leg.action.value == "buy":
            return leg.contract.delta
    return None


def _short_leg_primary_delta(candidate: StrategyCandidate) -> float | None:
    for leg in candidate.legs:
        if leg.action.value == "sell":
            return leg.contract.delta
    return None


def _spread_width(candidate: StrategyCandidate) -> float | None:
    strikes = [leg.contract.strike for leg in candidate.legs]
    if len(strikes) < 2:
        return None
    return max(strikes) - min(strikes)


def _dedupe(reason_codes: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for reason_code in reason_codes:
        if reason_code in seen:
            continue
        seen.add(reason_code)
        deduped.append(reason_code)
    return tuple(deduped)
