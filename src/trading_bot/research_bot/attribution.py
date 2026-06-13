from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Literal

from trading_bot.paper import PaperAccountState, PaperClosedTrade, PaperPosition

AttributionGroup = Literal["strategy", "symbol", "exit_reason"]


@dataclass(frozen=True)
class AttributionSummary:
    group: AttributionGroup
    key: str
    trade_count: int
    closed_trade_count: int
    open_trade_count: int
    realized_pnl: float
    win_rate: float | None
    avg_win: float | None
    avg_loss: float | None
    profit_factor: float | None
    expectancy: float | None
    avg_pnl_pct_of_max_loss: float | None
    stopout_rate: float | None
    profit_target_hit_rate: float | None
    capital_efficiency: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_paper_strategy_attribution(
    state: PaperAccountState,
) -> tuple[AttributionSummary, ...]:
    summaries: list[AttributionSummary] = []
    for group in ("strategy", "symbol", "exit_reason"):
        buckets: dict[str, list[PaperClosedTrade | PaperPosition]] = defaultdict(list)
        for trade in state.closed_trades:
            buckets[_group_key(group, trade)].append(trade)
        for position in state.open_positions:
            buckets[_group_key(group, position)].append(position)
        summaries.extend(_summarize_bucket(group, key, items) for key, items in buckets.items())
    return tuple(sorted(summaries, key=lambda item: (item.group, item.key)))


def _group_key(group: AttributionGroup, item: PaperClosedTrade | PaperPosition) -> str:
    position = item.position if isinstance(item, PaperClosedTrade) else item
    if group == "strategy":
        return position.strategy_name
    if group == "symbol":
        return position.underlying
    if isinstance(item, PaperClosedTrade):
        return item.exit_reason
    return "open"


def _summarize_bucket(
    group: AttributionGroup,
    key: str,
    items: list[PaperClosedTrade | PaperPosition],
) -> AttributionSummary:
    closed = [item for item in items if isinstance(item, PaperClosedTrade)]
    open_positions = [item for item in items if isinstance(item, PaperPosition)]
    wins = [trade.realized_pnl for trade in closed if trade.realized_pnl > 0]
    losses = [trade.realized_pnl for trade in closed if trade.realized_pnl < 0]
    realized_pnl = round(sum(trade.realized_pnl for trade in closed), 2)
    total_profit = sum(wins)
    total_loss_abs = abs(sum(losses))
    max_loss_reserved = sum(trade.position.max_loss for trade in closed)
    pnl_pct_values = [
        trade.realized_pnl / trade.position.max_loss
        for trade in closed
        if trade.position.max_loss > 0
    ]
    stopouts = [
        trade for trade in closed if "stop" in str(trade.exit_reason).lower()
    ]
    profit_targets = [
        trade for trade in closed if "profit" in str(trade.exit_reason).lower()
    ]

    return AttributionSummary(
        group=group,
        key=key,
        trade_count=len(items),
        closed_trade_count=len(closed),
        open_trade_count=len(open_positions),
        realized_pnl=realized_pnl,
        win_rate=_ratio(len(wins), len(closed)),
        avg_win=_average(wins),
        avg_loss=_average(losses),
        profit_factor=None if total_loss_abs == 0 else round(total_profit / total_loss_abs, 4),
        expectancy=None if not closed else round(realized_pnl / len(closed), 4),
        avg_pnl_pct_of_max_loss=_average(pnl_pct_values),
        stopout_rate=_ratio(len(stopouts), len(closed)),
        profit_target_hit_rate=_ratio(len(profit_targets), len(closed)),
        capital_efficiency=(
            None if max_loss_reserved <= 0 else round(realized_pnl / max_loss_reserved, 4)
        ),
    )


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)
