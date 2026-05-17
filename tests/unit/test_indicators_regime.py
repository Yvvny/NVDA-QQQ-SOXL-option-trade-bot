from datetime import UTC, datetime

from trading_bot.core.models import Candle
from trading_bot.indicators import ema, realized_volatility, rsi, vwap
from trading_bot.regime import MarketRegimeInput, RegimeClassifier, RegimeLabel


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
