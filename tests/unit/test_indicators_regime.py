from datetime import UTC, datetime

from trading_bot.core.models import Candle
from trading_bot.indicators import ema, realized_volatility, rsi, vwap
from trading_bot.regime import MarketRegimeInput, RegimeClassifier, RegimeLabel
from trading_bot.strategies.scoring import StrategyScoreInput, score_strategy_setup


def test_ema_seeds_with_sma_then_smooths():
    values = ema([1, 2, 3, 4, 5], period=3)

    assert values[:2] == [None, None]
    assert values[2] == 2
    assert values[-1] == 4


def test_rsi_returns_high_value_for_consistent_uptrend():
    values = rsi([1, 2, 3, 4, 5, 6], period=3)

    assert values[-1] == 100


def test_vwap_uses_cumulative_typical_price_volume():
    candles = [
        _candle(high=11, low=9, close=10, volume=100),
        _candle(high=12, low=10, close=11, volume=100),
    ]

    values = vwap(candles)

    assert values == [10, 10.5]


def test_realized_volatility_returns_annualized_value():
    closes = [100, 101, 99, 102, 103, 104]

    value = realized_volatility(closes, window=5)

    assert value is not None
    assert value > 0


def test_regime_classifier_returns_bull_trend_low_mid_iv():
    decision = RegimeClassifier().classify(
        MarketRegimeInput(
            qqq_close=510,
            qqq_ema20=500,
            qqq_ema50=480,
            spy_close=520,
            spy_ema20=510,
            spy_ema50=500,
            qqq_return_5d=0.02,
            qqq_return_20d=0.06,
            semiconductor_close=260,
            semiconductor_ema20=250,
            semiconductor_ema50=240,
            vix_level=16,
            iv_rank=25,
        )
    )

    assert decision.label == RegimeLabel.BULL_TREND_LOW_MID_IV
    assert decision.confidence > 0.80
    assert "bull_trend_confirmed" in decision.reason_codes


def test_regime_classifier_degrades_confidence_on_missing_inputs():
    decision = RegimeClassifier().classify(MarketRegimeInput(qqq_close=510))

    assert decision.confidence < 0.70
    assert "degraded_missing_inputs" in decision.reason_codes


def test_regime_classifier_detects_crash_risk_off():
    decision = RegimeClassifier().classify(
        MarketRegimeInput(
            qqq_close=450,
            qqq_ema20=480,
            qqq_ema50=500,
            qqq_return_5d=-0.10,
            qqq_return_20d=-0.15,
            vix_level=38,
        )
    )

    assert decision.label == RegimeLabel.CRASH_RISK_OFF
    assert "crash_risk_conditions" in decision.reason_codes


def test_regime_classifier_returns_unknown_when_intraday_confirmation_is_missing():
    decision = RegimeClassifier().classify(
        MarketRegimeInput(
            qqq_close=510,
            qqq_ema20=500,
            qqq_ema50=480,
            qqq_return_5d=0.02,
            qqq_return_20d=0.06,
            vix_level=16,
            require_intraday_confirmation=True,
        )
    )

    assert decision.label == RegimeLabel.UNKNOWN
    assert "unknown_missing_intraday_confirmation" in decision.reason_codes


def test_regime_classifier_detects_unstable_chop_from_vwap_crosses():
    decision = RegimeClassifier().classify(
        MarketRegimeInput(
            qqq_close=510,
            qqq_ema20=500,
            qqq_ema50=480,
            spy_close=520,
            spy_ema20=510,
            spy_ema50=500,
            qqq_return_5d=0.02,
            qqq_return_20d=0.06,
            target_close=120,
            qqq_vwap=509,
            target_vwap=119,
            qqq_vwap_cross_count=3,
            vix_level=16,
            iv_rank=25,
            require_intraday_confirmation=True,
        )
    )

    assert decision.label == RegimeLabel.UNSTABLE_CHOP
    assert "unstable_chop_conditions" in decision.reason_codes


def test_regime_classifier_marks_preservation_risk_mode():
    decision = RegimeClassifier().classify(
        MarketRegimeInput(
            qqq_close=510,
            qqq_ema20=500,
            qqq_ema50=480,
            spy_close=520,
            spy_ema20=510,
            spy_ema50=500,
            qqq_return_5d=0.02,
            qqq_return_20d=0.06,
            vix_level=16,
            iv_rank=25,
            current_equity=1705.5,
            starting_equity=2000,
            total_open_max_loss=507.5,
        )
    )

    assert "risk_mode_preservation_drawdown" in decision.reason_codes
    assert "risk_mode_preservation_open_risk" in decision.reason_codes


def test_unknown_regime_gets_hard_block_score_reason():
    score = score_strategy_setup(
        StrategyScoreInput(
            strategy_name="put_credit_spread",
            regime_label=RegimeLabel.UNKNOWN,
        )
    )

    assert score.breakdown.regime_fit == 0
    assert "regime_hard_block_unknown" in score.reason_codes


def _candle(high: float, low: float, close: float, volume: int) -> Candle:
    return Candle(
        symbol="QQQ",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )
