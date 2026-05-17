from __future__ import annotations

from collections.abc import Sequence

from trading_bot.core.models import Candle


def vwap(candles: Sequence[Candle]) -> list[float | None]:
    result: list[float | None] = []
    cumulative_price_volume = 0.0
    cumulative_volume = 0

    for candle in candles:
        typical_price = (candle.high + candle.low + candle.close) / 3
        cumulative_price_volume += typical_price * candle.volume
        cumulative_volume += candle.volume
        result.append(
            None if cumulative_volume == 0 else cumulative_price_volume / cumulative_volume
        )

    return result
