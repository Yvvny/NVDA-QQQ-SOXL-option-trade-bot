from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import date
from typing import Any

from trading_bot.config.settings import BotSettings
from trading_bot.core.enums import OptionType
from trading_bot.core.models import OptionContract, StrategyCandidate, UnderlyingQuote
from trading_bot.data.tastytrade_source import TastytradeMarketDataDiagnostics
from trading_bot.regime.classifier import RegimeLabel
from trading_bot.strategies.base import blocking_liquidity_warnings, contract_liquidity_warnings
from trading_bot.strategies.scoring import StrategyScoreInput, score_strategy_setup


def build_scan_diagnostics(
    *,
    settings: BotSettings,
    symbol: str,
    expiration: date,
    dte: int,
    underlying_quote: UnderlyingQuote | None,
    contracts: Sequence[OptionContract],
    regime_label: RegimeLabel,
    score_inputs: Iterable[StrategyScoreInput],
    candidates: Sequence[StrategyCandidate],
    market_data_diagnostics: TastytradeMarketDataDiagnostics | None = None,
) -> dict[str, Any]:
    eligible_contracts = _eligible_contracts(settings, contracts)
    candidate_by_strategy = {candidate.strategy_name: candidate for candidate in candidates}
    liquidity_warning_counts = _liquidity_warning_counts(settings, contracts)
    strategies = [
        _strategy_diagnostics(
            settings=settings,
            strategy_name=score_input.strategy_name,
            contracts=contracts,
            eligible_contracts=eligible_contracts,
            dte=dte,
            score_input=score_input,
            candidate=candidate_by_strategy.get(score_input.strategy_name),
        )
        for score_input in score_inputs
    ]

    return {
        "symbol": symbol,
        "expiration": expiration,
        "dte": dte,
        "regime_label": regime_label.value,
        "underlying_last": underlying_quote.last if underlying_quote is not None else None,
        "market_data": _market_data_summary(market_data_diagnostics),
        "contracts": {
            "received": len(contracts),
            "eligible": len(eligible_contracts),
            "by_type": _option_type_counts(contracts),
            "eligible_by_type": _option_type_counts(eligible_contracts),
        },
        "liquidity_blocks": dict(liquidity_warning_counts),
        "reason_codes": _scan_reason_codes(
            contracts=contracts,
            eligible_contracts=eligible_contracts,
            liquidity_warning_counts=liquidity_warning_counts,
            market_data_diagnostics=market_data_diagnostics,
            strategies=strategies,
        ),
        "strategies": strategies,
    }


def _strategy_diagnostics(
    *,
    settings: BotSettings,
    strategy_name: str,
    contracts: Sequence[OptionContract],
    eligible_contracts: Sequence[OptionContract],
    dte: int,
    score_input: StrategyScoreInput,
    candidate: StrategyCandidate | None,
) -> dict[str, Any]:
    score = score_strategy_setup(score_input)
    reason_codes = _candidate_block_reasons(
        settings=settings,
        strategy_name=strategy_name,
        contracts=contracts,
        eligible_contracts=eligible_contracts,
        dte=dte,
        score_total=score.total,
        score_reason_codes=score.reason_codes,
        candidate=candidate,
    )

    payload: dict[str, Any] = {
        "strategy_name": strategy_name,
        "score": score.total,
        "candidate_generated": candidate is not None,
        "reason_codes": reason_codes,
    }
    if candidate is not None:
        payload.update(
            {
                "max_profit": candidate.max_profit,
                "max_loss": candidate.max_loss,
                "expected_credit_or_debit": candidate.expected_credit_or_debit,
            }
        )
    return payload


def _candidate_block_reasons(
    *,
    settings: BotSettings,
    strategy_name: str,
    contracts: Sequence[OptionContract],
    eligible_contracts: Sequence[OptionContract],
    dte: int,
    score_total: float,
    score_reason_codes: tuple[str, ...],
    candidate: StrategyCandidate | None,
) -> tuple[str, ...]:
    if candidate is not None:
        return ("candidate_generated",)

    reasons: list[str] = []
    if score_total < settings.strategy.min_entry_score:
        reasons.append("score_below_min_entry_score")
    if "short_premium_blocked_crash_risk_off" in score_reason_codes:
        reasons.append("short_premium_blocked_crash_risk_off")
    if not eligible_contracts and contracts:
        reasons.append("all_contracts_failed_liquidity_filters")
    if not contracts:
        reasons.append("empty_option_chain")

    if strategy_name == "put_credit_spread":
        reasons.extend(_put_credit_reasons(settings, eligible_contracts, dte))
    elif strategy_name == "call_credit_spread":
        reasons.extend(_call_credit_reasons(eligible_contracts, dte, settings))
    elif strategy_name == "call_debit_spread":
        reasons.extend(_call_debit_reasons(settings, eligible_contracts, dte))
    elif strategy_name == "put_debit_spread":
        reasons.extend(_put_debit_reasons(settings, eligible_contracts, dte))
    else:
        reasons.append("strategy_not_in_live_scan_set")

    if not reasons:
        reasons.append("no_valid_spread_pair_after_price_width_checks")
    return _dedupe(reasons)


def _put_credit_reasons(
    settings: BotSettings,
    contracts: Sequence[OptionContract],
    dte: int,
) -> list[str]:
    reasons: list[str] = []
    if not settings.dte.short_premium_min <= dte <= settings.dte.short_premium_max:
        reasons.append("dte_out_of_short_premium_range")
    puts = [contract for contract in contracts if contract.option_type == OptionType.PUT]
    if not puts:
        reasons.append("no_eligible_put_contracts")
        return reasons
    short_puts = [
        contract
        for contract in puts
        if contract.delta is not None
        and settings.delta.short_premium_min_abs
        <= abs(contract.delta)
        <= settings.delta.short_premium_max_abs
    ]
    if not short_puts:
        reasons.append("no_short_put_delta_match")
    if short_puts and not _has_lower_protection_put(puts, short_puts):
        reasons.append("no_lower_put_with_1_to_5_width")
    if short_puts and _has_lower_protection_put(puts, short_puts):
        reasons.append("credit_pct_not_between_15_and_35_pct_width")
    return reasons


def _call_credit_reasons(
    contracts: Sequence[OptionContract],
    dte: int,
    settings: BotSettings,
) -> list[str]:
    reasons: list[str] = []
    if not 21 <= dte <= settings.dte.short_premium_max:
        reasons.append("dte_out_of_call_credit_range")
    calls = [contract for contract in contracts if contract.option_type == OptionType.CALL]
    if not calls:
        reasons.append("no_eligible_call_contracts")
        return reasons
    short_calls = [
        contract
        for contract in calls
        if contract.delta is not None and 0.15 <= abs(contract.delta) <= 0.30
    ]
    if not short_calls:
        reasons.append("no_short_call_delta_match")
    if short_calls and not _has_higher_protection_call(calls, short_calls):
        reasons.append("no_higher_call_with_1_to_5_width")
    if short_calls and _has_higher_protection_call(calls, short_calls):
        reasons.append("credit_pct_not_between_15_and_35_pct_width")
    return reasons


def _call_debit_reasons(
    settings: BotSettings,
    contracts: Sequence[OptionContract],
    dte: int,
) -> list[str]:
    reasons: list[str] = []
    if not settings.dte.trend_qqq_nvda_min <= dte <= settings.dte.trend_qqq_nvda_max:
        reasons.append("dte_out_of_trend_range")
    calls = [contract for contract in contracts if contract.option_type == OptionType.CALL]
    if not calls:
        reasons.append("no_eligible_call_contracts")
        return reasons
    long_calls = _trend_long_delta_matches(settings, calls)
    short_calls = _trend_short_delta_matches(settings, calls)
    if not long_calls:
        reasons.append("no_long_call_delta_match")
    if not short_calls:
        reasons.append("no_short_call_delta_match")
    if long_calls and short_calls and not _has_higher_short_call(long_calls, short_calls):
        reasons.append("no_short_call_above_long_call")
    if long_calls and short_calls and _has_higher_short_call(long_calls, short_calls):
        reasons.append("reward_risk_below_1_2_or_invalid_debit")
    return reasons


def _put_debit_reasons(
    settings: BotSettings,
    contracts: Sequence[OptionContract],
    dte: int,
) -> list[str]:
    reasons: list[str] = []
    if not settings.dte.trend_qqq_nvda_min <= dte <= settings.dte.trend_qqq_nvda_max:
        reasons.append("dte_out_of_trend_range")
    puts = [contract for contract in contracts if contract.option_type == OptionType.PUT]
    if not puts:
        reasons.append("no_eligible_put_contracts")
        return reasons
    long_puts = _trend_long_delta_matches(settings, puts)
    short_puts = _trend_short_delta_matches(settings, puts)
    if not long_puts:
        reasons.append("no_long_put_delta_match")
    if not short_puts:
        reasons.append("no_short_put_delta_match")
    if long_puts and short_puts and not _has_lower_short_put(long_puts, short_puts):
        reasons.append("no_short_put_below_long_put")
    if long_puts and short_puts and _has_lower_short_put(long_puts, short_puts):
        reasons.append("reward_risk_below_1_2_or_invalid_debit")
    return reasons


def _eligible_contracts(
    settings: BotSettings,
    contracts: Sequence[OptionContract],
) -> list[OptionContract]:
    return [
        contract for contract in contracts if not blocking_liquidity_warnings(contract, settings)
    ]


def _liquidity_warning_counts(
    settings: BotSettings,
    contracts: Sequence[OptionContract],
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for contract in contracts:
        counts.update(contract_liquidity_warnings(contract, settings))
    return counts


def _option_type_counts(contracts: Sequence[OptionContract]) -> dict[str, int]:
    counts = Counter(contract.option_type.value for contract in contracts)
    return dict(sorted(counts.items()))


def _market_data_summary(
    market_data_diagnostics: TastytradeMarketDataDiagnostics | None,
) -> dict[str, Any] | None:
    if market_data_diagnostics is None:
        return None
    return asdict(market_data_diagnostics)


def _scan_reason_codes(
    *,
    contracts: Sequence[OptionContract],
    eligible_contracts: Sequence[OptionContract],
    liquidity_warning_counts: Counter[str],
    market_data_diagnostics: TastytradeMarketDataDiagnostics | None,
    strategies: Sequence[dict[str, Any]],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if market_data_diagnostics is not None and market_data_diagnostics.market_data_incomplete:
        reasons.append("market_data_incomplete")
    if not contracts:
        reasons.append("empty_option_chain")
    elif not eligible_contracts:
        reasons.append("no_eligible_contracts_after_liquidity_filters")

    for reason, count in liquidity_warning_counts.most_common(3):
        if count:
            reasons.append(reason)

    if not any(strategy["candidate_generated"] for strategy in strategies):
        reasons.append("no_strategy_candidate_generated")

    return _dedupe(reasons)


def _has_lower_protection_put(
    puts: Sequence[OptionContract],
    short_puts: Sequence[OptionContract],
) -> bool:
    return any(1 <= short_put.strike - put.strike <= 5 for short_put in short_puts for put in puts)


def _has_higher_protection_call(
    calls: Sequence[OptionContract],
    short_calls: Sequence[OptionContract],
) -> bool:
    return any(
        1 <= call.strike - short_call.strike <= 5 for short_call in short_calls for call in calls
    )


def _trend_long_delta_matches(
    settings: BotSettings,
    contracts: Sequence[OptionContract],
) -> list[OptionContract]:
    return [
        contract
        for contract in contracts
        if contract.delta is not None
        and settings.delta.trend_long_min_abs
        <= abs(contract.delta)
        <= settings.delta.trend_long_max_abs
    ]


def _trend_short_delta_matches(
    settings: BotSettings,
    contracts: Sequence[OptionContract],
) -> list[OptionContract]:
    return [
        contract
        for contract in contracts
        if contract.delta is not None
        and settings.delta.trend_short_min_abs
        <= abs(contract.delta)
        <= settings.delta.trend_short_max_abs
    ]


def _has_higher_short_call(
    long_calls: Sequence[OptionContract],
    short_calls: Sequence[OptionContract],
) -> bool:
    return any(
        short_call.strike > long_call.strike
        for long_call in long_calls
        for short_call in short_calls
    )


def _has_lower_short_put(
    long_puts: Sequence[OptionContract],
    short_puts: Sequence[OptionContract],
) -> bool:
    return any(
        short_put.strike < long_put.strike for long_put in long_puts for short_put in short_puts
    )


def _dedupe(reason_codes: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for reason_code in reason_codes:
        if reason_code in seen:
            continue
        seen.add(reason_code)
        deduped.append(reason_code)
    return tuple(deduped)
