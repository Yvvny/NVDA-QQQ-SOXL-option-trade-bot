from datetime import date

from trading_bot.core.enums import OptionType
from trading_bot.core.models import OptionContract
from trading_bot.regime import RegimeLabel
from trading_bot.strategies import (
    ShortPremiumEngine,
    StrategyScoreInput,
    TrendParticipationEngine,
    score_strategy_setup,
)

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
            _contract("put", 450, -0.25, 0.40, 0.50),
            _contract("put", 449, -0.10, 0.15, 0.25),
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


def test_call_credit_spread_candidate_has_correct_legs_and_max_loss():
    score = _score("call_credit_spread", RegimeLabel.BEAR_TREND_HIGH_IV)

    candidate = ShortPremiumEngine().generate_call_credit_spread(
        contracts=[
            _contract("call", 455, 0.25, 0.35, 0.45),
            _contract("call", 456, 0.10, 0.15, 0.25),
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
            _contract("call", 450, 0.55, 0.75, 0.85),
            _contract("call", 452, 0.30, 0.25, 0.35),
        ],
        underlying="QQQ",
        dte=21,
        score=score,
    )

    assert candidate is not None
    assert [leg.action.value for leg in candidate.legs] == ["buy", "sell"]
    assert [leg.contract.strike for leg in candidate.legs] == [450, 452]
    assert candidate.max_profit == 150
    assert candidate.max_loss == 50


def test_put_debit_spread_candidate_has_correct_legs_and_max_loss():
    score = _score("put_debit_spread", RegimeLabel.BEAR_TREND_HIGH_IV, iv_rank=30)

    candidate = TrendParticipationEngine().generate_put_debit_spread(
        contracts=[
            _contract("put", 450, -0.55, 0.75, 0.85),
            _contract("put", 448, -0.30, 0.25, 0.35),
        ],
        underlying="QQQ",
        dte=21,
        score=score,
    )

    assert candidate is not None
    assert [leg.action.value for leg in candidate.legs] == ["buy", "sell"]
    assert [leg.contract.strike for leg in candidate.legs] == [450, 448]
    assert candidate.max_profit == 150
    assert candidate.max_loss == 50


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
            _contract("put", 450, -0.25, 0.40, 0.50),
            _contract("put", 449, -0.10, 0.15, 0.25),
        ],
        underlying="QQQ",
        dte=30,
        score=low_score,
    )

    assert candidate is None


def _score(
    strategy_name: str,
    regime_label: RegimeLabel = RegimeLabel.BULL_TREND_HIGH_IV,
    iv_rank: float = 55,
):
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
        )
    )


def _contract(
    option_type: str,
    strike: float,
    delta: float,
    bid: float,
    ask: float,
) -> OptionContract:
    return OptionContract(
        symbol=f"QQQ {EXPIRATION.isoformat()} {strike} {option_type}",
        underlying="QQQ",
        expiration=EXPIRATION,
        strike=strike,
        option_type=OptionType(option_type),
        bid=bid,
        ask=ask,
        mid=(bid + ask) / 2,
        delta=delta,
        volume=100,
        open_interest=1000,
    )
