from dataclasses import replace
from datetime import date, datetime

from trading_bot.config.settings import load_settings
from trading_bot.core.enums import OptionType
from trading_bot.core.models import Candle, OptionContract, ScoreBreakdown
from trading_bot.core.time_utils import NEW_YORK_TIME_ZONE
from trading_bot.regime import RegimeLabel
from trading_bot.strategies import (
    EntryTimingContext,
    ShortPremiumEngine,
    StrategyScoreInput,
    StrategyScoreResult,
    TrendParticipationEngine,
    evaluate_debit_price_action_confirmation,
    evaluate_entry_timing,
    qqq_put_credit_spread_quality_score,
    score_strategy_setup,
)
from trading_bot.strategies.base import contract_liquidity_warnings

EXPIRATION = date(2026, 6, 19)


def test_strategy_score_high_for_preferred_liquid_setup():
    score = score_strategy_setup(
        StrategyScoreInput(
            strategy_name="put_credit_spread",
            regime_label=RegimeLabel.BULL_TREND_HIGH_IV,
            iv_rank=55,
            bid_ask_pct_of_mid=0.08,
            volume=100,
            open_interest=1000,
            price_above_ema20=True,
            price_above_vwap=True,
            breakout_or_pullback_confirmed=True,
        )
    )

    assert score.total >= 80
    assert "regime_fit_preferred" in score.reason_codes


def test_strategy_score_penalizes_poor_liquidity_and_event_risk():
    score = score_strategy_setup(
        StrategyScoreInput(
            strategy_name="put_credit_spread",
            regime_label=RegimeLabel.BULL_TREND_HIGH_IV,
            iv_rank=55,
            bid_ask_pct_of_mid=0.30,
            volume=1,
            open_interest=5,
            major_event_within_24h=True,
        )
    )

    assert score.total < 70
    assert "liquidity_wide_spread" in score.reason_codes
    assert "event_risk_penalty" in score.reason_codes


def test_put_credit_spread_candidate_has_correct_legs_and_max_loss():
    score = _score("put_credit_spread")

    candidate = ShortPremiumEngine().generate_put_credit_spread(
        contracts=[
            _contract("put", 450, -0.25, 0.43, 0.47),
            _contract("put", 449, -0.10, 0.19, 0.21),
        ],
        underlying="QQQ",
        dte=30,
        score=score,
    )

    assert candidate is not None
    assert [leg.action.value for leg in candidate.legs] == ["sell", "buy"]
    assert [leg.contract.strike for leg in candidate.legs] == [450, 449]
    assert candidate.max_profit == 25
    assert candidate.max_loss == 75


def test_put_credit_spread_prefers_best_reward_risk_within_dynamic_budget():
    score = _score("put_credit_spread")

    candidate = ShortPremiumEngine().generate_put_credit_spread(
        contracts=[
            _contract("put", 450, -0.25, 2.10, 2.20),
            _contract("put", 449, -0.18, 1.75, 1.85),
            _contract("put", 448, -0.10, 1.41, 1.51),
            _contract("put", 447, -0.05, 1.35, 1.45),
        ],
        underlying="SPY",
        dte=30,
        score=score,
    )

    assert candidate is not None
    assert [leg.contract.strike for leg in candidate.legs] == [450, 448]
    assert candidate.max_loss <= 400


def test_credit_spread_rejects_low_planned_reward_risk_without_high_quality_override():
    score = _medium_credit_score()
    settings = _settings_with_credit_planned_reward_risk_threshold(0.30)

    candidate = ShortPremiumEngine(settings).generate_put_credit_spread(
        contracts=[
            _contract("put", 450, -0.18, 0.43, 0.47),
            _contract("put", 449, -0.08, 0.19, 0.21),
        ],
        underlying="QQQ",
        dte=30,
        score=score,
    )

    assert score.total < 80
    assert candidate is None


def test_credit_spread_high_quality_override_adds_planned_reward_risk_warning():
    score = _score("put_credit_spread")
    settings = _settings_with_credit_planned_reward_risk_threshold(0.30)

    candidate = ShortPremiumEngine(settings).generate_put_credit_spread(
        contracts=[
            _contract("put", 450, -0.18, 0.43, 0.47),
            _contract("put", 449, -0.08, 0.19, 0.21),
        ],
        underlying="QQQ",
        dte=30,
        score=score,
    )

    assert candidate is not None
    assert "reward_risk_credit_planned_rr_low_high_quality_override" in (
        candidate.reason_codes
    )


def test_qqq_put_credit_spread_quality_prefers_lower_short_delta():
    score = _score("put_credit_spread")

    candidate = ShortPremiumEngine().generate_put_credit_spread(
        contracts=[
            _contract("put", 450, -0.25, 0.43, 0.47),
            _contract("put", 449, -0.08, 0.19, 0.21),
            _contract("put", 448, -0.18, 0.43, 0.47),
            _contract("put", 447, -0.08, 0.19, 0.21),
        ],
        underlying="QQQ",
        dte=30,
        score=score,
    )

    assert candidate is not None
    assert [leg.contract.strike for leg in candidate.legs] == [448, 447]
    assert "qqq_pcs_quality_selector_active" in candidate.reason_codes
    assert "qqq_pcs_short_delta_preferred" in candidate.reason_codes


def test_qqq_put_credit_spread_quality_penalizes_thin_atr_cushion():
    score = _score("put_credit_spread")

    candidate = ShortPremiumEngine().generate_put_credit_spread(
        contracts=[
            _contract("put", 454, -0.18, 0.43, 0.47),
            _contract("put", 453, -0.08, 0.19, 0.21),
            _contract("put", 452, -0.18, 0.43, 0.47),
            _contract("put", 451, -0.08, 0.19, 0.21),
        ],
        underlying="QQQ",
        dte=30,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0, price=455, vwap=None, atr=2),
    )

    assert candidate is not None
    assert [leg.contract.strike for leg in candidate.legs] == [452, 451]
    assert "qqq_pcs_atr_cushion_strong" in candidate.reason_codes
    assert "qqq_pcs_atr_cushion_too_thin" not in candidate.reason_codes


def test_qqq_put_credit_spread_quality_prefers_short_strike_below_vwap():
    score = _score("put_credit_spread")

    candidate = ShortPremiumEngine().generate_put_credit_spread(
        contracts=[
            _contract("put", 452, -0.18, 0.43, 0.47),
            _contract("put", 451, -0.08, 0.19, 0.21),
            _contract("put", 450, -0.18, 0.43, 0.47),
            _contract("put", 449, -0.08, 0.19, 0.21),
        ],
        underlying="QQQ",
        dte=30,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0, price=455, vwap=451, atr=2),
    )

    assert candidate is not None
    assert [leg.contract.strike for leg in candidate.legs] == [450, 449]
    assert "qqq_pcs_short_strike_below_vwap" in candidate.reason_codes


def test_qqq_put_credit_spread_quality_prefers_tighter_bid_ask_spread():
    score = _score("put_credit_spread")

    candidate = ShortPremiumEngine().generate_put_credit_spread(
        contracts=[
            _contract("put", 452, -0.18, 0.42, 0.48),
            _contract("put", 451, -0.08, 0.185, 0.215),
            _contract("put", 450, -0.18, 0.43, 0.47),
            _contract("put", 449, -0.08, 0.19, 0.21),
        ],
        underlying="QQQ",
        dte=30,
        score=score,
    )

    assert candidate is not None
    assert [leg.contract.strike for leg in candidate.legs] == [450, 449]
    assert any(
        reason in candidate.reason_codes
        for reason in ("qqq_pcs_liquidity_strong", "qqq_pcs_liquidity_acceptable")
    )


def test_qqq_put_credit_spread_quality_prefers_small_account_width():
    score = _score("put_credit_spread")

    candidate = ShortPremiumEngine().generate_put_credit_spread(
        contracts=[
            _contract("put", 452, -0.18, 0.93, 0.97),
            _contract("put", 449, -0.08, 0.19, 0.21),
            _contract("put", 448, -0.18, 0.43, 0.47),
            _contract("put", 447, -0.08, 0.19, 0.21),
        ],
        underlying="QQQ",
        dte=30,
        score=score,
    )

    assert candidate is not None
    assert [leg.contract.strike for leg in candidate.legs] == [448, 447]
    assert "qqq_pcs_width_small_account_preferred" in candidate.reason_codes


def test_non_qqq_put_credit_spread_keeps_existing_selector_reason_codes():
    score = _score("put_credit_spread")

    candidate = ShortPremiumEngine().generate_put_credit_spread(
        contracts=[
            _contract("put", 450, -0.25, 0.43, 0.47),
            _contract("put", 449, -0.08, 0.19, 0.21),
            _contract("put", 448, -0.18, 0.43, 0.47),
            _contract("put", 447, -0.08, 0.19, 0.21),
        ],
        underlying="SPY",
        dte=30,
        score=score,
    )

    assert candidate is not None
    assert "qqq_pcs_quality_selector_active" not in candidate.reason_codes


def test_qqq_put_credit_spread_quality_score_reports_unavailable_timing_data():
    score = _score("put_credit_spread")
    candidate = ShortPremiumEngine().generate_put_credit_spread(
        contracts=[
            _contract("put", 450, -0.18, 0.43, 0.47),
            _contract("put", 449, -0.08, 0.19, 0.21),
        ],
        underlying="SPY",
        dte=30,
        score=score,
    )

    assert candidate is not None
    quality_score, reasons = qqq_put_credit_spread_quality_score(
        candidate=candidate,
        underlying_price=None,
        vwap=None,
        ema20=None,
        atr=None,
        risk_cap=400,
    )

    assert quality_score > 0
    assert "qqq_pcs_atr_cushion_unavailable" in reasons
    assert "qqq_pcs_vwap_unavailable" in reasons


def test_call_credit_spread_candidate_has_correct_legs_and_max_loss():
    score = _score("call_credit_spread", RegimeLabel.BEAR_TREND_HIGH_IV)

    candidate = ShortPremiumEngine().generate_call_credit_spread(
        contracts=[
            _contract("call", 455, 0.25, 0.38, 0.42),
            _contract("call", 456, 0.10, 0.19, 0.21),
        ],
        underlying="QQQ",
        dte=30,
        score=score,
    )

    assert candidate is not None
    assert [leg.action.value for leg in candidate.legs] == ["sell", "buy"]
    assert [leg.contract.strike for leg in candidate.legs] == [455, 456]
    assert candidate.max_profit == 20
    assert candidate.max_loss == 80


def test_call_debit_spread_candidate_has_correct_legs_and_max_loss():
    score = _score("call_debit_spread", RegimeLabel.BULL_TREND_LOW_MID_IV, iv_rank=30)

    candidate = TrendParticipationEngine().generate_call_debit_spread(
        contracts=[
            _contract("call", 450, 0.55, 0.78, 0.82),
            _contract("call", 452, 0.30, 0.29, 0.31),
        ],
        underlying="QQQ",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0),
    )

    assert candidate is not None
    assert [leg.action.value for leg in candidate.legs] == ["buy", "sell"]
    assert [leg.contract.strike for leg in candidate.legs] == [450, 452]
    assert candidate.max_profit == 150
    assert candidate.max_loss == 50


def test_generic_debit_spread_rejects_reward_risk_below_1_35():
    score = _score("call_debit_spread", RegimeLabel.BULL_TREND_LOW_MID_IV, iv_rank=30)

    candidate = TrendParticipationEngine().generate_call_debit_spread(
        contracts=[
            _contract("call", 450, 0.55, 0.99, 1.01),
            _contract("call", 452, 0.30, 0.12, 0.14),
        ],
        underlying="QQQ",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0),
    )

    assert candidate is None


def test_nvda_call_debit_spread_is_disabled_by_default():
    score = _score("call_debit_spread", RegimeLabel.BULL_TREND_LOW_MID_IV, iv_rank=30)

    candidate = TrendParticipationEngine().generate_call_debit_spread(
        contracts=[
            _contract("call", 450, 0.55, 0.78, 0.82),
            _contract("call", 452, 0.30, 0.29, 0.31),
        ],
        underlying="NVDA",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0),
        iv_rank=30,
    )

    assert candidate is None


def test_nvda_put_debit_spread_is_disabled_by_default():
    score = _score("put_debit_spread", RegimeLabel.BEAR_TREND_HIGH_IV, iv_rank=30)

    candidate = TrendParticipationEngine().generate_put_debit_spread(
        contracts=[
            _contract("put", 450, -0.55, 0.78, 0.82),
            _contract("put", 448, -0.30, 0.29, 0.31),
        ],
        underlying="NVDA",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0),
        iv_rank=30,
    )

    assert candidate is None


def test_soxl_call_debit_spread_rules_are_unchanged_by_nvda_gate():
    score = _score("call_debit_spread", RegimeLabel.BULL_TREND_LOW_MID_IV, iv_rank=30)

    candidate = TrendParticipationEngine().generate_call_debit_spread(
        contracts=[
            _contract("call", 450, 0.55, 0.78, 0.82),
            _contract("call", 452, 0.30, 0.29, 0.31),
        ],
        underlying="SOXL",
        dte=14,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0),
        iv_rank=30,
    )

    assert candidate is not None


def test_nvda_debit_spread_experimental_gate_requires_score_80():
    settings = _settings_with_nvda_debit_experimental_enabled()
    score = _medium_debit_score()

    candidate = TrendParticipationEngine(settings).generate_call_debit_spread(
        contracts=[
            _contract("call", 450, 0.55, 0.78, 0.82),
            _contract("call", 452, 0.30, 0.29, 0.31),
        ],
        underlying="NVDA",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0),
        iv_rank=30,
    )

    assert candidate is None


def test_nvda_debit_spread_experimental_gate_rejects_missing_iv_rank():
    settings = _settings_with_nvda_debit_experimental_enabled()

    candidate = TrendParticipationEngine(settings).generate_call_debit_spread(
        contracts=[
            _contract("call", 450, 0.55, 0.78, 0.82),
            _contract("call", 452, 0.30, 0.29, 0.31),
        ],
        underlying="NVDA",
        dte=21,
        score=_high_debit_score(),
        entry_timing=_entry_timing(hour=10, minute=0),
        iv_rank=None,
    )

    assert candidate is None


def test_nvda_debit_spread_experimental_gate_rejects_high_iv_rank():
    settings = _settings_with_nvda_debit_experimental_enabled()

    candidate = TrendParticipationEngine(settings).generate_call_debit_spread(
        contracts=[
            _contract("call", 450, 0.55, 0.78, 0.82),
            _contract("call", 452, 0.30, 0.29, 0.31),
        ],
        underlying="NVDA",
        dte=21,
        score=_high_debit_score(),
        entry_timing=_entry_timing(hour=10, minute=0),
        iv_rank=55,
    )

    assert candidate is None


def test_nvda_debit_spread_experimental_gate_rejects_reward_risk_below_1_5():
    settings = _settings_with_nvda_debit_experimental_enabled()

    candidate = TrendParticipationEngine(settings).generate_call_debit_spread(
        contracts=[
            _contract("call", 450, 0.55, 0.99, 1.01),
            _contract("call", 452, 0.30, 0.14, 0.16),
        ],
        underlying="NVDA",
        dte=21,
        score=_high_debit_score(),
        entry_timing=_entry_timing(hour=10, minute=0),
        iv_rank=30,
    )

    assert candidate is None


def test_call_debit_spread_requires_price_action_confirmation():
    score = score_strategy_setup(
        StrategyScoreInput(
            strategy_name="call_debit_spread",
            regime_label=RegimeLabel.BULL_TREND_LOW_MID_IV,
            iv_rank=30,
            bid_ask_pct_of_mid=0.08,
            volume=100,
            open_interest=1000,
            price_above_ema20=True,
            price_above_vwap=True,
            breakout_or_pullback_confirmed=False,
        )
    )

    candidate = TrendParticipationEngine().generate_call_debit_spread(
        contracts=[
            _contract("call", 450, 0.55, 0.78, 0.82),
            _contract("call", 452, 0.30, 0.29, 0.31),
        ],
        underlying="QQQ",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0),
    )

    assert score.total >= 80
    assert "price_action_confirmed" not in score.reason_codes
    assert candidate is None


def test_call_debit_spread_rejects_missing_timing_context():
    score = _score("call_debit_spread", RegimeLabel.BULL_TREND_LOW_MID_IV, iv_rank=30)

    candidate = TrendParticipationEngine().generate_call_debit_spread(
        contracts=[
            _contract("call", 450, 0.55, 0.78, 0.82),
            _contract("call", 452, 0.30, 0.29, 0.31),
        ],
        underlying="QQQ",
        dte=21,
        score=score,
        entry_timing=None,
    )
    decision = evaluate_entry_timing(
        strategy_name="call_debit_spread",
        score_reason_codes=score.reason_codes,
        context=None,
        settings=load_settings(env={}),
    )

    assert candidate is None
    assert decision.approved is False
    assert "timing_context_missing" in decision.reason_codes


def test_debit_timing_rejects_missing_timestamp():
    decision = evaluate_entry_timing(
        strategy_name="call_debit_spread",
        score_reason_codes=("price_action_confirmed",),
        context=EntryTimingContext(
            timestamp=None,
            underlying_price=100.0,
            vwap=100.0,
            atr=2.0,
            recent_candles=_clear_candles(),
        ),
        settings=load_settings(env={}),
    )

    assert decision.approved is False
    assert "timing_timestamp_missing" in decision.reason_codes


def test_debit_timing_rejects_missing_price_vwap_or_atr():
    base_context = {
        "timestamp": datetime(2026, 6, 4, 10, 0, tzinfo=NEW_YORK_TIME_ZONE),
        "underlying_price": 100.0,
        "vwap": 100.0,
        "atr": 2.0,
        "recent_candles": _clear_candles(),
    }

    for missing_key in ("underlying_price", "vwap", "atr"):
        values = dict(base_context)
        values[missing_key] = None
        decision = evaluate_entry_timing(
            strategy_name="call_debit_spread",
            score_reason_codes=("price_action_confirmed",),
            context=EntryTimingContext(**values),
            settings=load_settings(env={}),
        )

        assert decision.approved is False
        assert "timing_anti_chase_price_data_missing" in decision.reason_codes


def test_debit_timing_rejects_invalid_atr():
    decision = evaluate_entry_timing(
        strategy_name="call_debit_spread",
        score_reason_codes=("price_action_confirmed",),
        context=_entry_timing(hour=10, minute=0, atr=0),
        settings=load_settings(env={}),
    )

    assert decision.approved is False
    assert "timing_anti_chase_price_data_missing" in decision.reason_codes


def test_debit_timing_rejects_insufficient_recent_candles():
    decision = evaluate_entry_timing(
        strategy_name="call_debit_spread",
        score_reason_codes=("price_action_confirmed",),
        context=_entry_timing(hour=10, minute=0, candles=()),
        settings=load_settings(env={}),
    )

    assert decision.approved is False
    assert "timing_anti_chase_candle_data_missing" in decision.reason_codes


def test_credit_spread_timing_gate_is_not_required():
    decision = evaluate_entry_timing(
        strategy_name="put_credit_spread",
        score_reason_codes=(),
        context=None,
        settings=load_settings(env={}),
    )

    assert decision.approved is True
    assert decision.reason_codes == ("timing_not_required",)


def test_call_debit_spread_opening_cooldown_rejects_until_0950():
    score = _score("call_debit_spread", RegimeLabel.BULL_TREND_LOW_MID_IV, iv_rank=30)
    engine = TrendParticipationEngine()
    contracts = [
        _contract("call", 450, 0.55, 0.78, 0.82),
        _contract("call", 452, 0.30, 0.29, 0.31),
    ]

    at_0935 = engine.generate_call_debit_spread(
        contracts=contracts,
        underlying="QQQ",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=9, minute=35),
    )
    at_0949 = engine.generate_call_debit_spread(
        contracts=contracts,
        underlying="QQQ",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=9, minute=49),
    )
    at_0950 = engine.generate_call_debit_spread(
        contracts=contracts,
        underlying="QQQ",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=9, minute=50),
    )

    assert at_0935 is None
    assert at_0949 is None
    assert at_0950 is not None
    assert "timing_opening_cooldown_clear" in at_0950.reason_codes


def test_call_debit_spread_anti_chase_rejects_vwap_atr_extension():
    score = _score("call_debit_spread", RegimeLabel.BULL_TREND_LOW_MID_IV, iv_rank=30)

    candidate = TrendParticipationEngine().generate_call_debit_spread(
        contracts=[
            _contract("call", 450, 0.55, 0.78, 0.82),
            _contract("call", 452, 0.30, 0.29, 0.31),
        ],
        underlying="QQQ",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0, price=104.0, vwap=100.0, atr=2.0),
    )

    assert candidate is None


def test_call_debit_spread_vwap_atr_warning_does_not_hard_reject():
    score = _score("call_debit_spread", RegimeLabel.BULL_TREND_LOW_MID_IV, iv_rank=30)

    candidate = TrendParticipationEngine().generate_call_debit_spread(
        contracts=[
            _contract("call", 450, 0.55, 0.78, 0.82),
            _contract("call", 452, 0.30, 0.29, 0.31),
        ],
        underlying="QQQ",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0, price=102.5, vwap=100.0, atr=2.0),
    )

    assert candidate is not None
    assert "timing_call_debit_extended_above_vwap_atr_warning" in candidate.reason_codes


def test_put_debit_spread_anti_chase_rejects_vwap_atr_extension():
    score = _score("put_debit_spread", RegimeLabel.BEAR_TREND_HIGH_IV, iv_rank=30)

    candidate = TrendParticipationEngine().generate_put_debit_spread(
        contracts=[
            _contract("put", 450, -0.55, 0.78, 0.82),
            _contract("put", 448, -0.30, 0.29, 0.31),
        ],
        underlying="QQQ",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0, price=96.0, vwap=100.0, atr=2.0),
    )

    assert candidate is None


def test_call_debit_spread_anti_chase_rejects_three_strong_bullish_candles():
    score = _score("call_debit_spread", RegimeLabel.BULL_TREND_LOW_MID_IV, iv_rank=30)
    candles = tuple(
        Candle(
            symbol="QQQ",
            timestamp=datetime(2026, 6, 4, 9, 35 + index * 5, tzinfo=NEW_YORK_TIME_ZONE),
            open=100.0 + index,
            high=102.5 + index,
            low=99.5 + index,
            close=102.5 + index,
            volume=1000,
        )
        for index in range(3)
    )

    candidate = TrendParticipationEngine().generate_call_debit_spread(
        contracts=[
            _contract("call", 450, 0.55, 0.78, 0.82),
            _contract("call", 452, 0.30, 0.29, 0.31),
        ],
        underlying="QQQ",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0, candles=candles),
    )

    assert candidate is None


def test_call_debit_spread_prefers_best_reward_risk_within_dynamic_budget():
    score = score_strategy_setup(
        StrategyScoreInput(
            strategy_name="call_debit_spread",
            regime_label=RegimeLabel.RANGE_LOW_IV,
            iv_rank=None,
            bid_ask_pct_of_mid=0.08,
            volume=100,
            open_interest=1000,
            breakout_or_pullback_confirmed=True,
            entry_timing=_entry_timing(hour=10, minute=0, candles=_call_breakout_candles()),
        )
    )

    candidate = TrendParticipationEngine().generate_call_debit_spread(
        contracts=[
            _contract("call", 220, 0.50, 8.45, 8.55),
            _contract("call", 223, 0.35, 7.25, 7.35),
            _contract("call", 240, 0.22, 2.95, 3.05),
        ],
        underlying="QQQ",
        dte=29,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0),
    )

    assert candidate is not None
    assert [leg.contract.strike for leg in candidate.legs] == [220, 223]
    assert candidate.max_loss <= 400


def test_put_debit_spread_candidate_has_correct_legs_and_max_loss():
    score = _score("put_debit_spread", RegimeLabel.BEAR_TREND_HIGH_IV, iv_rank=30)

    candidate = TrendParticipationEngine().generate_put_debit_spread(
        contracts=[
            _contract("put", 450, -0.55, 0.78, 0.82),
            _contract("put", 448, -0.30, 0.29, 0.31),
        ],
        underlying="QQQ",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0),
    )

    assert candidate is not None
    assert [leg.action.value for leg in candidate.legs] == ["buy", "sell"]
    assert [leg.contract.strike for leg in candidate.legs] == [450, 448]
    assert candidate.max_profit == 150
    assert candidate.max_loss == 50


def test_put_debit_spread_anti_chase_rejects_three_strong_bearish_candles():
    score = _score("put_debit_spread", RegimeLabel.BEAR_TREND_HIGH_IV, iv_rank=30)
    candles = tuple(
        Candle(
            symbol="QQQ",
            timestamp=datetime(2026, 6, 4, 9, 35 + index * 5, tzinfo=NEW_YORK_TIME_ZONE),
            open=100.0 - index,
            high=100.5 - index,
            low=98.0 - index,
            close=98.0 - index,
            volume=1000,
        )
        for index in range(3)
    )

    candidate = TrendParticipationEngine().generate_put_debit_spread(
        contracts=[
            _contract("put", 450, -0.55, 0.78, 0.82),
            _contract("put", 448, -0.30, 0.29, 0.31),
        ],
        underlying="QQQ",
        dte=21,
        score=score,
        entry_timing=_entry_timing(hour=10, minute=0, candles=candles),
    )

    assert candidate is None


def test_candidate_generation_rejects_score_below_threshold():
    low_score = score_strategy_setup(
        StrategyScoreInput(
            strategy_name="put_credit_spread",
            regime_label=RegimeLabel.CRASH_RISK_OFF,
            iv_rank=10,
            bid_ask_pct_of_mid=0.30,
            volume=1,
            open_interest=5,
            major_event_within_24h=True,
        )
    )

    candidate = ShortPremiumEngine().generate_put_credit_spread(
        contracts=[
            _contract("put", 450, -0.25, 0.43, 0.47),
            _contract("put", 449, -0.10, 0.19, 0.21),
        ],
        underlying="QQQ",
        dte=30,
        score=low_score,
    )

    assert candidate is None


def test_call_debit_price_action_confirms_bullish_breakout():
    confirmed, reasons = evaluate_debit_price_action_confirmation(
        strategy_name="call_debit_spread",
        context=_entry_timing(hour=10, minute=0, candles=_call_breakout_candles()),
        lookback_candles=5,
        min_body_atr_multiple=0.25,
        vwap_reclaim_tolerance_atr_multiple=0.20,
    )

    assert confirmed is True
    assert reasons == ("price_action_call_breakout_confirmed",)


def test_call_debit_price_action_confirms_vwap_reclaim():
    confirmed, reasons = evaluate_debit_price_action_confirmation(
        strategy_name="call_debit_spread",
        context=_entry_timing(hour=10, minute=0, candles=_call_vwap_reclaim_candles()),
        lookback_candles=5,
        min_body_atr_multiple=0.25,
        vwap_reclaim_tolerance_atr_multiple=0.20,
    )

    assert confirmed is True
    assert reasons == ("price_action_call_vwap_reclaim_confirmed",)


def test_call_debit_price_action_rejects_small_body_and_wrong_vwap_side():
    small_body, small_body_reasons = evaluate_debit_price_action_confirmation(
        strategy_name="call_debit_spread",
        context=_entry_timing(hour=10, minute=0, candles=_call_small_body_candles()),
        lookback_candles=5,
        min_body_atr_multiple=0.25,
        vwap_reclaim_tolerance_atr_multiple=0.20,
    )
    wrong_side, wrong_side_reasons = evaluate_debit_price_action_confirmation(
        strategy_name="call_debit_spread",
        context=_entry_timing(hour=10, minute=0, candles=_call_wrong_vwap_side_candles()),
        lookback_candles=5,
        min_body_atr_multiple=0.25,
        vwap_reclaim_tolerance_atr_multiple=0.20,
    )

    assert small_body is False
    assert small_body_reasons == ("price_action_unconfirmed_small_body",)
    assert wrong_side is False
    assert wrong_side_reasons == ("price_action_unconfirmed_wrong_vwap_side",)


def test_put_debit_price_action_confirms_bearish_breakdown():
    confirmed, reasons = evaluate_debit_price_action_confirmation(
        strategy_name="put_debit_spread",
        context=_entry_timing(hour=10, minute=0, candles=_put_breakdown_candles()),
        lookback_candles=5,
        min_body_atr_multiple=0.25,
        vwap_reclaim_tolerance_atr_multiple=0.20,
    )

    assert confirmed is True
    assert reasons == ("price_action_put_breakdown_confirmed",)


def test_put_debit_price_action_confirms_vwap_rejection():
    confirmed, reasons = evaluate_debit_price_action_confirmation(
        strategy_name="put_debit_spread",
        context=_entry_timing(hour=10, minute=0, candles=_put_vwap_rejection_candles()),
        lookback_candles=5,
        min_body_atr_multiple=0.25,
        vwap_reclaim_tolerance_atr_multiple=0.20,
    )

    assert confirmed is True
    assert reasons == ("price_action_put_vwap_rejection_confirmed",)


def test_put_debit_price_action_rejects_small_body_and_wrong_vwap_side():
    small_body, small_body_reasons = evaluate_debit_price_action_confirmation(
        strategy_name="put_debit_spread",
        context=_entry_timing(hour=10, minute=0, candles=_put_small_body_candles()),
        lookback_candles=5,
        min_body_atr_multiple=0.25,
        vwap_reclaim_tolerance_atr_multiple=0.20,
    )
    wrong_side, wrong_side_reasons = evaluate_debit_price_action_confirmation(
        strategy_name="put_debit_spread",
        context=_entry_timing(hour=10, minute=0, candles=_put_wrong_vwap_side_candles()),
        lookback_candles=5,
        min_body_atr_multiple=0.25,
        vwap_reclaim_tolerance_atr_multiple=0.20,
    )

    assert small_body is False
    assert small_body_reasons == ("price_action_unconfirmed_small_body",)
    assert wrong_side is False
    assert wrong_side_reasons == ("price_action_unconfirmed_wrong_vwap_side",)


def test_debit_price_action_rejects_insufficient_confirmation_candles():
    confirmed, reasons = evaluate_debit_price_action_confirmation(
        strategy_name="call_debit_spread",
        context=_entry_timing(hour=10, minute=0, candles=_clear_candles()),
        lookback_candles=5,
        min_body_atr_multiple=0.25,
        vwap_reclaim_tolerance_atr_multiple=0.20,
    )

    assert confirmed is False
    assert reasons == ("price_action_unconfirmed_insufficient_candles",)


def _score(
    strategy_name: str,
    regime_label: RegimeLabel = RegimeLabel.BULL_TREND_HIGH_IV,
    iv_rank: float = 55,
):
    entry_timing = _confirmed_entry_timing_for_strategy(strategy_name)
    return score_strategy_setup(
        StrategyScoreInput(
            strategy_name=strategy_name,
            regime_label=regime_label,
            iv_rank=iv_rank,
            bid_ask_pct_of_mid=0.08,
            volume=100,
            open_interest=1000,
            price_above_ema20=True,
            price_above_vwap=True,
            breakout_or_pullback_confirmed=True,
            entry_timing=entry_timing,
        )
    )


def _high_debit_score() -> StrategyScoreResult:
    return StrategyScoreResult(
        breakdown=ScoreBreakdown(
            regime_fit=30,
            volatility_edge=25,
            liquidity_quality=20,
            price_action=15,
            event_risk=10,
        ),
        reason_codes=(
            "regime_fit_preferred",
            "volatility_edge_high_iv",
            "liquidity_tight_spread",
            "price_action_confirmed",
            "event_risk_clear",
        ),
    )


def _medium_debit_score() -> StrategyScoreResult:
    return StrategyScoreResult(
        breakdown=ScoreBreakdown(
            regime_fit=18,
            volatility_edge=15,
            liquidity_quality=18,
            price_action=10,
            event_risk=5,
        ),
        reason_codes=(
            "regime_fit_reduced",
            "volatility_edge_available",
            "liquidity_tight_spread",
            "price_action_confirmed",
            "event_risk_clear",
        ),
    )


def _medium_credit_score() -> StrategyScoreResult:
    return StrategyScoreResult(
        breakdown=ScoreBreakdown(
            regime_fit=30,
            volatility_edge=15,
            liquidity_quality=15,
            price_action=5,
            event_risk=10,
        ),
        reason_codes=(
            "regime_fit_preferred",
            "volatility_edge_available",
            "liquidity_activity_ok",
            "price_action_neutral",
            "event_risk_clear",
        ),
    )


def _settings_with_nvda_debit_experimental_enabled():
    settings = load_settings(env={})
    return replace(
        settings,
        strategy=replace(
            settings.strategy,
            nvda_debit_spread_experimental_enabled=True,
        ),
    )


def _settings_with_credit_planned_reward_risk_threshold(threshold: float):
    settings = load_settings(env={})
    return replace(
        settings,
        strategy=replace(
            settings.strategy,
            credit_spread_min_planned_reward_risk=threshold,
        ),
    )


def _confirmed_entry_timing_for_strategy(strategy_name: str) -> EntryTimingContext | None:
    if strategy_name == "call_debit_spread":
        return _entry_timing(hour=10, minute=0, candles=_call_breakout_candles())
    if strategy_name == "put_debit_spread":
        return _entry_timing(hour=10, minute=0, candles=_put_breakdown_candles())
    return None


def _contract(
    option_type: str,
    strike: float,
    delta: float,
    bid: float,
    ask: float,
    expiration: date = EXPIRATION,
    volume: int | None = 100,
    open_interest: int | None = 1000,
    allow_missing_activity_data: bool = False,
) -> OptionContract:
    return OptionContract(
        symbol=f"QQQ {EXPIRATION.isoformat()} {strike} {option_type}",
        underlying="QQQ",
        expiration=expiration,
        strike=strike,
        option_type=OptionType(option_type),
        bid=bid,
        ask=ask,
        mid=(bid + ask) / 2,
        delta=delta,
        volume=volume,
        open_interest=open_interest,
        allow_missing_activity_data=allow_missing_activity_data,
    )


def _entry_timing(
    *,
    hour: int,
    minute: int,
    price: float | None = 100.0,
    vwap: float | None = 100.0,
    atr: float | None = 2.0,
    candles: tuple[Candle, ...] | None = None,
) -> EntryTimingContext:
    return EntryTimingContext(
        timestamp=datetime(2026, 6, 4, hour, minute, tzinfo=NEW_YORK_TIME_ZONE),
        underlying_price=price,
        vwap=vwap,
        atr=atr,
        recent_candles=_clear_candles() if candles is None else candles,
    )


def _clear_candles() -> tuple[Candle, ...]:
    return tuple(
        Candle(
            symbol="QQQ",
            timestamp=datetime(2026, 6, 4, 9, 35 + index * 5, tzinfo=NEW_YORK_TIME_ZONE),
            open=100.0,
            high=101.0,
            low=99.5,
            close=100.2,
            volume=1000,
        )
        for index in range(3)
    )


def _candles_from_ohlc(rows: tuple[tuple[float, float, float, float], ...]) -> tuple[Candle, ...]:
    return tuple(
        Candle(
            symbol="QQQ",
            timestamp=datetime(2026, 6, 4, 9, 30 + index * 5, tzinfo=NEW_YORK_TIME_ZONE),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=1000,
        )
        for index, (open_price, high, low, close) in enumerate(rows)
    )


def _call_breakout_candles() -> tuple[Candle, ...]:
    return _candles_from_ohlc(
        (
            (99.6, 100.2, 99.2, 99.8),
            (99.8, 100.4, 99.5, 100.0),
            (100.0, 100.6, 99.7, 100.2),
            (100.1, 100.8, 99.9, 100.3),
            (100.2, 101.0, 100.0, 100.4),
            (101.0, 102.7, 100.8, 102.5),
        )
    )


def _call_vwap_reclaim_candles() -> tuple[Candle, ...]:
    return _candles_from_ohlc(
        (
            (101.4, 103.0, 101.0, 102.2),
            (102.0, 103.2, 100.2, 101.4),
            (101.2, 103.1, 99.8, 100.5),
            (100.4, 103.3, 99.9, 100.6),
            (100.5, 103.4, 100.1, 100.8),
            (100.8, 102.4, 100.6, 101.8),
        )
    )


def _call_small_body_candles() -> tuple[Candle, ...]:
    return _candles_from_ohlc(
        (
            (99.6, 100.2, 99.2, 99.8),
            (99.8, 100.4, 99.5, 100.0),
            (100.0, 100.6, 99.7, 100.2),
            (100.1, 100.8, 99.9, 100.3),
            (100.2, 101.0, 100.0, 100.4),
            (100.95, 102.0, 100.8, 101.1),
        )
    )


def _call_wrong_vwap_side_candles() -> tuple[Candle, ...]:
    return _candles_from_ohlc(
        (
            (99.6, 100.2, 99.2, 99.8),
            (99.8, 100.4, 99.5, 100.0),
            (100.0, 100.6, 99.7, 100.2),
            (100.1, 100.8, 99.9, 100.3),
            (100.2, 101.0, 100.0, 100.4),
            (98.8, 99.8, 98.5, 99.4),
        )
    )


def _put_breakdown_candles() -> tuple[Candle, ...]:
    return _candles_from_ohlc(
        (
            (100.4, 100.8, 99.8, 100.2),
            (100.2, 100.5, 99.5, 99.9),
            (99.9, 100.1, 99.2, 99.7),
            (99.7, 99.9, 98.9, 99.4),
            (99.5, 99.7, 98.5, 99.0),
            (98.8, 99.0, 96.8, 97.2),
        )
    )


def _put_vwap_rejection_candles() -> tuple[Candle, ...]:
    return _candles_from_ohlc(
        (
            (98.4, 99.2, 98.0, 98.8),
            (98.8, 100.1, 98.4, 99.2),
            (99.2, 100.3, 98.8, 99.4),
            (99.4, 100.2, 98.9, 99.3),
            (99.3, 100.1, 98.7, 99.1),
            (99.0, 99.2, 97.7, 98.2),
        )
    )


def _put_small_body_candles() -> tuple[Candle, ...]:
    return _candles_from_ohlc(
        (
            (100.4, 100.8, 99.8, 100.2),
            (100.2, 100.5, 99.5, 99.9),
            (99.9, 100.1, 99.2, 99.7),
            (99.7, 99.9, 98.9, 99.4),
            (99.5, 99.7, 98.5, 99.0),
            (99.05, 99.2, 98.8, 98.9),
        )
    )


def _put_wrong_vwap_side_candles() -> tuple[Candle, ...]:
    return _candles_from_ohlc(
        (
            (100.4, 100.8, 99.8, 100.2),
            (100.2, 100.5, 99.5, 99.9),
            (99.9, 100.1, 99.2, 99.7),
            (99.7, 99.9, 98.9, 99.4),
            (99.5, 99.7, 98.5, 99.0),
            (100.4, 101.2, 100.0, 100.9),
        )
    )


def test_iron_condor_candidate_has_four_legs_and_defined_risk():
    score = _score("iron_condor", RegimeLabel.RANGE_HIGH_IV)

    from trading_bot.strategies import NeutralRangeEngine

    candidate = NeutralRangeEngine().generate_iron_condor(
        contracts=[
            _contract("put", 450, -0.20, 0.24, 0.26),
            _contract("put", 449, -0.08, 0.095, 0.105),
            _contract("call", 460, 0.20, 0.24, 0.26),
            _contract("call", 461, 0.08, 0.095, 0.105),
        ],
        underlying="QQQ",
        dte=35,
        score=score,
    )

    assert candidate is not None
    assert candidate.strategy_name == "iron_condor"
    assert [leg.action.value for leg in candidate.legs] == ["sell", "buy", "sell", "buy"]
    assert candidate.max_profit == 30
    assert candidate.max_loss == 70


def test_short_premium_is_blocked_in_crash_regime_even_with_otherwise_good_score():
    score = _score("put_credit_spread", RegimeLabel.CRASH_RISK_OFF)

    candidate = ShortPremiumEngine().generate_put_credit_spread(
        contracts=[
            _contract("put", 450, -0.25, 0.43, 0.47),
            _contract("put", 449, -0.10, 0.19, 0.21),
        ],
        underlying="QQQ",
        dte=30,
        score=score,
    )

    assert candidate is None
    assert "short_premium_blocked_crash_risk_off" in score.reason_codes


def test_real_data_contracts_allow_missing_activity_metadata_without_liquidity_rejection():
    warnings = contract_liquidity_warnings(
        _contract(
            "put",
            450,
            -0.25,
            0.43,
            0.47,
            volume=None,
            open_interest=None,
            allow_missing_activity_data=True,
        )
    )

    assert "missing_volume_metadata" in warnings
    assert "missing_open_interest_metadata" in warnings
    assert "low_or_missing_volume" not in warnings
    assert "low_or_missing_open_interest" not in warnings


def test_put_credit_spread_candidate_allows_missing_activity_metadata_for_real_data():
    score = _score("put_credit_spread")

    candidate = ShortPremiumEngine().generate_put_credit_spread(
        contracts=[
            _contract(
                "put",
                450,
                -0.25,
                0.43,
                0.47,
                volume=None,
                open_interest=None,
                allow_missing_activity_data=True,
            ),
            _contract(
                "put",
                449,
                -0.10,
                0.19,
                0.21,
                volume=None,
                open_interest=None,
                allow_missing_activity_data=True,
            ),
        ],
        underlying="QQQ",
        dte=30,
        score=score,
    )

    assert candidate is not None


def test_calendar_and_diagonal_spreads_are_defined_risk_debit_candidates():
    from datetime import date as _date

    from trading_bot.strategies import CalendarDiagonalEngine

    score = _score("calendar_spread", RegimeLabel.RANGE_LOW_IV, iv_rank=20)
    engine = CalendarDiagonalEngine()
    front = _date(2026, 1, 15)
    back = _date(2026, 2, 20)
    contracts = [
        _contract("call", 500, 0.50, 0.39, 0.41, expiration=front),
        _contract("call", 500, 0.55, 0.99, 1.01, expiration=back),
        _contract("call", 502, 0.30, 0.29, 0.31, expiration=front),
    ]

    calendar = engine.generate_calendar_spread(
        contracts,
        underlying="QQQ",
        front_dte=14,
        score=score,
        as_of=_date(2026, 1, 1),
    )
    diagonal = engine.generate_diagonal_spread(
        contracts,
        underlying="QQQ",
        front_dte=14,
        score=_score("diagonal_spread", RegimeLabel.RANGE_LOW_IV, iv_rank=20),
        as_of=_date(2026, 1, 1),
    )

    assert calendar is not None
    assert calendar.strategy_name == "calendar_spread"
    assert calendar.max_loss == 60
    assert diagonal is not None
    assert diagonal.strategy_name == "diagonal_spread"
    assert diagonal.max_loss == 70
