from __future__ import annotations

from dataclasses import dataclass

from trading_bot.backtest.slippage import apply_slippage


@dataclass(frozen=True)
class FillAssumption:
    bid_ask_spread: float = 0.10
    slippage: float = 0.01


def estimate_fill_price(mid: float, side: str, assumption: FillAssumption | None = None) -> float:
    assumption = assumption or FillAssumption()
    half_spread = assumption.bid_ask_spread / 2
    spread_adjusted = mid + half_spread if side == "buy" else max(0.0, mid - half_spread)
    return round(apply_slippage(spread_adjusted, assumption.slippage, side), 2)
