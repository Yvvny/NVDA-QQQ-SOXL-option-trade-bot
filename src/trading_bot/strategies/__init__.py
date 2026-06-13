"""Strategy candidate generation engines."""

from trading_bot.strategies.calendar_diagonal import CalendarDiagonalEngine
from trading_bot.strategies.neutral_range import NeutralRangeEngine
from trading_bot.strategies.scoring import (
    StrategyScoreInput,
    StrategyScoreResult,
    evaluate_debit_price_action_confirmation,
    score_strategy_setup,
)
from trading_bot.strategies.selector import StrategySelector
from trading_bot.strategies.short_premium import (
    ShortPremiumEngine,
    qqq_put_credit_spread_quality_score,
)
from trading_bot.strategies.timing_filters import EntryTimingContext, evaluate_entry_timing
from trading_bot.strategies.trend_participation import TrendParticipationEngine

__all__ = [
    "CalendarDiagonalEngine",
    "EntryTimingContext",
    "NeutralRangeEngine",
    "ShortPremiumEngine",
    "StrategyScoreInput",
    "StrategyScoreResult",
    "StrategySelector",
    "TrendParticipationEngine",
    "evaluate_debit_price_action_confirmation",
    "evaluate_entry_timing",
    "qqq_put_credit_spread_quality_score",
    "score_strategy_setup",
]
