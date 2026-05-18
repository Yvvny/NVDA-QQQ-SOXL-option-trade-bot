"""Strategy candidate generation engines."""

from trading_bot.strategies.calendar_diagonal import CalendarDiagonalEngine
from trading_bot.strategies.neutral_range import NeutralRangeEngine
from trading_bot.strategies.scoring import (
    StrategyScoreInput,
    StrategyScoreResult,
    score_strategy_setup,
)
from trading_bot.strategies.selector import StrategySelector
from trading_bot.strategies.short_premium import ShortPremiumEngine
from trading_bot.strategies.trend_participation import TrendParticipationEngine

__all__ = [
    "CalendarDiagonalEngine",
    "NeutralRangeEngine",
    "ShortPremiumEngine",
    "StrategyScoreInput",
    "StrategyScoreResult",
    "StrategySelector",
    "TrendParticipationEngine",
    "score_strategy_setup",
]
