from __future__ import annotations

import math
from collections.abc import Sequence


def realized_volatility(
    closes: Sequence[float],
    window: int = 20,
    trading_days: int = 252,
) -> float | None:
    if window <= 1:
        raise ValueError("Realized volatility window must be greater than 1.")
    if len(closes) <= window:
        return None

    returns = [
        math.log(closes[index] / closes[index - 1])
        for index in range(len(closes) - window, len(closes))
        if closes[index] > 0 and closes[index - 1] > 0
    ]
    if len(returns) < 2:
        return None

    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(trading_days)
