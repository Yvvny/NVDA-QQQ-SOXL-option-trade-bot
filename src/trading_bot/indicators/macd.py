from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from trading_bot.indicators.ema import ema


@dataclass(frozen=True)
class MACDResult:
    macd_line: list[float | None]
    signal_line: list[float | None]
    histogram: list[float | None]


def macd(
    values: Sequence[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> MACDResult:
    if fast_period <= 0 or slow_period <= 0 or signal_period <= 0:
        raise ValueError("MACD periods must be positive.")
    if fast_period >= slow_period:
        raise ValueError("MACD fast period must be less than slow period.")

    fast = ema(values, fast_period)
    slow = ema(values, slow_period)
    line: list[float | None] = [
        None if fast_value is None or slow_value is None else fast_value - slow_value
        for fast_value, slow_value in zip(fast, slow, strict=True)
    ]
    signal = _ema_sparse(line, signal_period)
    histogram = [
        None if line_value is None or signal_value is None else line_value - signal_value
        for line_value, signal_value in zip(line, signal, strict=True)
    ]
    return MACDResult(macd_line=line, signal_line=signal, histogram=histogram)


def _ema_sparse(values: Sequence[float | None], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    observed: list[tuple[int, float]] = [
        (index, value) for index, value in enumerate(values) if value is not None
    ]
    if len(observed) < period:
        return result

    seed_index = observed[period - 1][0]
    seed = sum(value for _, value in observed[:period]) / period
    result[seed_index] = seed
    previous = seed
    multiplier = 2 / (period + 1)

    for index, value in observed[period:]:
        previous = (value - previous) * multiplier + previous
        result[index] = previous

    return result
