from __future__ import annotations


def apply_slippage(price: float, slippage: float, side: str) -> float:
    if price < 0:
        raise ValueError("Price cannot be negative.")
    if slippage < 0:
        raise ValueError("Slippage cannot be negative.")
    if side not in {"buy", "sell"}:
        raise ValueError("Side must be 'buy' or 'sell'.")
    return price + slippage if side == "buy" else max(0.0, price - slippage)
