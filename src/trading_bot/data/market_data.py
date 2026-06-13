from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from trading_bot.config.settings import BotSettings, load_settings
from trading_bot.core.models import OptionContract, UnderlyingQuote
from trading_bot.core.time_utils import now_new_york
from trading_bot.strategies.base import (
    bid_ask_pct_of_mid,
    blocking_liquidity_warnings,
)


class MarketDataError(RuntimeError):
    pass


class MarketDataProvider(Protocol):
    def fetch_snapshot(self, symbol: str, target_dte: int) -> MarketDataSnapshot: ...


@dataclass(frozen=True)
class MarketDataSnapshot:
    symbol: str
    expiration: object
    dte: int
    underlying_quote: UnderlyingQuote | None
    option_contracts: tuple[OptionContract, ...]
    source: str = "unknown"
    captured_at: datetime | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SnapshotQualityReport:
    usable: bool
    reason_codes: tuple[str, ...]
    contract_count: int
    liquid_contract_count: int
    missing_bid_ask_count: int
    wide_spread_count: int


class RetryingMarketDataProvider:
    def __init__(
        self,
        provider: MarketDataProvider,
        *,
        settings: BotSettings | None = None,
        attempts: int = 3,
        retry_delay_seconds: float = 0.5,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if attempts <= 0:
            raise ValueError("attempts must be positive.")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative.")
        self.provider = provider
        self.settings = settings or load_settings()
        self.attempts = attempts
        self.retry_delay_seconds = retry_delay_seconds
        self.now = now or now_new_york

    def fetch_snapshot(self, symbol: str, target_dte: int) -> MarketDataSnapshot:
        errors: list[str] = []
        last_snapshot: MarketDataSnapshot | None = None

        for attempt in range(1, self.attempts + 1):
            try:
                snapshot = normalize_snapshot(
                    self.provider.fetch_snapshot(symbol, target_dte),
                    source=getattr(self.provider, "source_name", self.provider.__class__.__name__),
                    now=self.now,
                )
            except Exception as exc:  # noqa: BLE001 - preserve provider failure context.
                errors.append(f"attempt_{attempt}_error:{exc.__class__.__name__}")
            else:
                report = validate_snapshot(snapshot, self.settings)
                if report.usable:
                    return replace(
                        snapshot,
                        warnings=tuple((*snapshot.warnings, *report.reason_codes)),
                    )
                last_snapshot = snapshot
                errors.append(f"attempt_{attempt}_quality:{','.join(report.reason_codes)}")

            if attempt < self.attempts and self.retry_delay_seconds:
                time.sleep(self.retry_delay_seconds)

        if last_snapshot is not None:
            report = validate_snapshot(last_snapshot, self.settings)
            raise MarketDataError(
                "Market data snapshot failed validation: "
                + ",".join((*report.reason_codes, *errors))
            )
        raise MarketDataError("Market data provider failed: " + ",".join(errors))


class CachedMarketDataProvider:
    def __init__(
        self,
        provider: MarketDataProvider,
        *,
        ttl_seconds: float = 30.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds cannot be negative.")
        self.provider = provider
        self.ttl_seconds = ttl_seconds
        self.now = now or now_new_york
        self._cache: dict[tuple[str, int], tuple[datetime, MarketDataSnapshot]] = {}

    def fetch_snapshot(self, symbol: str, target_dte: int) -> MarketDataSnapshot:
        key = (symbol.upper(), target_dte)
        current_time = self.now()
        cached = self._cache.get(key)
        if cached is not None:
            captured_at, snapshot = cached
            age = (current_time - captured_at).total_seconds()
            if age <= self.ttl_seconds:
                return replace(snapshot, warnings=(*snapshot.warnings, "cache_hit"))

        snapshot = normalize_snapshot(
            self.provider.fetch_snapshot(symbol, target_dte),
            source=getattr(self.provider, "source_name", self.provider.__class__.__name__),
            now=self.now,
        )
        self._cache[key] = (current_time, snapshot)
        return snapshot


def normalize_snapshot(
    snapshot,
    *,
    source: str,
    now: Callable[[], datetime] | None = None,
) -> MarketDataSnapshot:
    current_time = (now or now_new_york)()
    if isinstance(snapshot, MarketDataSnapshot):
        if snapshot.captured_at is not None:
            return snapshot
        return replace(snapshot, captured_at=current_time)

    return MarketDataSnapshot(
        symbol=str(snapshot.symbol).upper(),
        expiration=snapshot.expiration,
        dte=int(snapshot.dte),
        underlying_quote=snapshot.underlying_quote,
        option_contracts=tuple(snapshot.option_contracts),
        source=source,
        captured_at=current_time,
        warnings=tuple(getattr(snapshot, "warnings", ())),
    )


def validate_snapshot(
    snapshot: MarketDataSnapshot,
    settings: BotSettings | None = None,
) -> SnapshotQualityReport:
    settings = settings or load_settings()
    reason_codes: list[str] = []
    contracts = snapshot.option_contracts

    if not contracts:
        reason_codes.append("empty_option_chain")

    missing_bid_ask_count = sum(
        1 for contract in contracts if contract.bid is None or contract.ask is None
    )
    wide_spread_count = sum(
        1
        for contract in contracts
        if (spread := bid_ask_pct_of_mid(contract)) is not None
        and spread > settings.liquidity.max_bid_ask_pct_of_mid
    )
    liquid_contracts = tuple(_liquid_contracts(contracts, settings))

    if missing_bid_ask_count == len(contracts) and contracts:
        reason_codes.append("all_contracts_missing_bid_ask")
    if not liquid_contracts and contracts:
        reason_codes.append("no_liquid_contracts")
    if snapshot.underlying_quote is None:
        reason_codes.append("missing_underlying_quote")
    if snapshot.dte < settings.dte.forbidden_dte_min:
        reason_codes.append("forbidden_dte")

    usable = bool(contracts) and bool(liquid_contracts) and "forbidden_dte" not in reason_codes
    return SnapshotQualityReport(
        usable=usable,
        reason_codes=tuple(reason_codes),
        contract_count=len(contracts),
        liquid_contract_count=len(liquid_contracts),
        missing_bid_ask_count=missing_bid_ask_count,
        wide_spread_count=wide_spread_count,
    )


def _liquid_contracts(
    contracts: Sequence[OptionContract],
    settings: BotSettings,
) -> tuple[OptionContract, ...]:
    return tuple(
        contract
        for contract in contracts
        if not blocking_liquidity_warnings(contract, settings)
    )
