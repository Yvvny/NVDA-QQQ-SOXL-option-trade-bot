from __future__ import annotations

from enum import StrEnum


class OptionType(StrEnum):
    CALL = "call"
    PUT = "put"


class OptionAction(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    LIMIT = "limit"
    MARKET = "market"


class TradeStatus(StrEnum):
    CANDIDATE = "candidate"
    REJECTED = "rejected"
    APPROVED = "approved"
    DRY_RUN = "dry_run"
    OPEN = "open"
    CLOSED = "closed"
