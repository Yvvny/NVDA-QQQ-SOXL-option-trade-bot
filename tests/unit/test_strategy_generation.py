from datetime import date

from trading_bot.core.enums import OptionType
from trading_bot.core.models import OptionContract
from trading_bot.regime import RegimeLabel
from trading_bot.strategies.base import contract_liquidity_warnings
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
    score = score_strategy_setup(
        StrategyScoreInput(
            strategy_name="put_credit_spread",
            regime_label=RegimeLabel.BULL_TREND_HIGH_IV,
            iv_rank=None,
            bid_ask_pct_of_mid=0.08,
            volume=100,
            open_interest=1000,
        )
    )

    candidate = ShortPremiumEngine().generate_put_credit_spread(
        contracts=[
            _contract("put", 450, -0.25, 2.10, 2.20),
            _contract("put", 449, -0.18, 1.75, 1.85),
            _contract("put", 448, -0.10, 1.41, 1.51),
            _contract("put", 447, -0.05, 1.35, 1.45),
        ],
        underlying="QQQ",
        dte=30,
        score=score,
    )

    assert candidate is not None
    assert [leg.contract.strike for leg in candidate.legs] == [450, 448]
    assert candidate.max_loss <= 400


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
    )

    assert candidate is not None
    assert [leg.action.value for leg in candidate.legs] == ["buy", "sell"]
    assert [leg.contract.strike for leg in candidate.legs] == [450, 452]
    assert candidate.max_profit == 150
    assert candidate.max_loss == 50


def test_call_debit_spread_prefers_best_reward_risk_within_dynamic_budget():
    score = score_strategy_setup(
        StrategyScoreInput(
            strategy_name="call_debit_spread",
            regime_label=RegimeLabel.RANGE_LOW_IV,
            iv_rank=None,
            bid_ask_pct_of_mid=0.08,
            volume=100,
            open_interest=1000,
        )
    )

    candidate = TrendParticipationEngine().generate_call_debit_spread(
        contracts=[
            _contract("call", 220, 0.50, 8.45, 8.55),
            _contract("call", 223, 0.35, 7.15, 7.25),
            _contract("call", 240, 0.22, 2.95, 3.05),
        ],
        underlying="NVDA",
        dte=29,
        score=score,
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
            _contract("put", 450, -0.25, 0.43, 0.47),
            _contract("put", 449, -0.10, 0.19, 0.21),
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
