from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

from trading_bot.core.enums import OptionType
from trading_bot.core.models import OptionContract, UnderlyingQuote
from trading_bot.data import (
    CachedMarketDataProvider,
    MarketDataError,
    MarketDataSnapshot,
    RetryingMarketDataProvider,
    validate_snapshot,
)


def test_snapshot_validation_requires_liquid_option_contracts():
    snapshot = _snapshot((_contract(volume=0),))

    report = validate_snapshot(snapshot)

    assert report.usable is False
    assert "no_liquid_contracts" in report.reason_codes


def test_retrying_market_data_provider_recovers_from_transient_failure():
    provider = _FlakyProvider([RuntimeError("temporary"), _snapshot((_contract(),))])

    snapshot = RetryingMarketDataProvider(
        provider,
        attempts=2,
        retry_delay_seconds=0,
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    ).fetch_snapshot("QQQ", 30)

    assert snapshot.symbol == "QQQ"
    assert provider.calls == 2


def test_retrying_market_data_provider_rejects_bad_snapshot():
    provider = _FlakyProvider([_snapshot((_contract(volume=0),))])

    with pytest.raises(MarketDataError, match="no_liquid_contracts"):
        RetryingMarketDataProvider(provider, attempts=1, retry_delay_seconds=0).fetch_snapshot(
            "QQQ", 30
        )


def test_cached_market_data_provider_returns_recent_snapshot():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    provider = _FlakyProvider([_snapshot((_contract(),))])
    cached = CachedMarketDataProvider(provider, ttl_seconds=60, now=lambda: now)

    first = cached.fetch_snapshot("QQQ", 30)
    second = cached.fetch_snapshot("QQQ", 30)

    assert first.symbol == "QQQ"
    assert "cache_hit" in second.warnings
    assert provider.calls == 1


@dataclass
class _FlakyProvider:
    responses: list
    calls: int = 0

    def fetch_snapshot(self, symbol: str, target_dte: int):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _snapshot(contracts):
    return MarketDataSnapshot(
        symbol="QQQ",
        expiration=date(2026, 6, 19),
        dte=30,
        underlying_quote=UnderlyingQuote(
            symbol="QQQ",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            bid=500,
            ask=500.1,
            last=500.05,
        ),
        option_contracts=tuple(contracts),
    )


def _contract(volume: int = 100) -> OptionContract:
    return OptionContract(
        symbol="QQQ 2026-06-19 450 put",
        underlying="QQQ",
        expiration=date(2026, 6, 19),
        strike=450,
        option_type=OptionType.PUT,
        bid=0.45,
        ask=0.50,
        mid=0.475,
        delta=-0.20,
        volume=volume,
        open_interest=1000,
    )
