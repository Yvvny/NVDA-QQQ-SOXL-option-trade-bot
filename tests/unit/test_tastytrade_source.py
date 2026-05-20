from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from trading_bot.core.enums import OptionType
from trading_bot.data.tastytrade_source import (
    TastytradeSdkDataSource,
    _has_minimum_market_data,
    _required_option_event_count,
    contracts_from_sdk_options,
    underlying_quote_from_streamer,
)


def test_contracts_from_sdk_options_maps_quotes_and_greeks():
    option = _SdkOption(
        symbol="QQQ  260619P00450000",
        underlying_symbol="QQQ",
        expiration_date=date(2026, 6, 19),
        strike_price=Decimal("450"),
        option_type="P",
        streamer_symbol=".QQQ260619P450",
    )
    quote = _Quote(event_symbol=".QQQ260619P450", bid_price=0.45, ask_price=0.55)
    greek = _Greek(
        event_symbol=".QQQ260619P450",
        delta=-0.25,
        gamma=0.01,
        theta=-0.02,
        vega=0.03,
        volatility=0.40,
    )

    contracts = contracts_from_sdk_options(
        [option],
        {quote.event_symbol: quote},
        {greek.event_symbol: greek},
    )

    assert len(contracts) == 1
    assert contracts[0].option_type == OptionType.PUT
    assert contracts[0].bid == 0.45
    assert contracts[0].ask == 0.55
    assert contracts[0].mid == 0.5
    assert contracts[0].delta == -0.25
    assert contracts[0].iv == 0.40


def test_underlying_quote_from_streamer_maps_bid_ask_to_last_mid():
    quote = _Quote(event_symbol="QQQ", bid_price=510.0, ask_price=510.2)

    underlying_quote = underlying_quote_from_streamer("QQQ", {"QQQ": quote})

    assert underlying_quote is not None
    assert underlying_quote.symbol == "QQQ"
    assert underlying_quote.last == 510.1


def test_tastytrade_source_from_env_uses_explicit_mapping():
    source = TastytradeSdkDataSource.from_env(
        {
            "TASTYTRADE_USERNAME": "user",
            "TASTYTRADE_PASSWORD": "pass",
            "TASTYTRADE_IS_TEST": "false",
        }
    )

    assert source.username == "user"
    assert source.password == "pass"
    assert source.is_test is False


def test_tastytrade_source_waits_for_meaningful_option_event_count():
    greek_symbols = tuple(f".QQQ260619C{i}" for i in range(80))
    quotes = {symbol: object() for symbol in greek_symbols[:29]}
    greeks = {symbol: object() for symbol in greek_symbols[:30]}

    assert _required_option_event_count(80) == 30
    assert not _has_minimum_market_data(
        ("QQQ", *greek_symbols),
        greek_symbols,
        quotes,
        greeks,
    )

    quotes[greek_symbols[29]] = object()

    assert _has_minimum_market_data(
        ("QQQ", *greek_symbols),
        greek_symbols,
        quotes,
        greeks,
    )


@dataclass(frozen=True)
class _SdkOption:
    symbol: str
    underlying_symbol: str
    expiration_date: date
    strike_price: Decimal
    option_type: str
    streamer_symbol: str


@dataclass(frozen=True)
class _Quote:
    event_symbol: str
    bid_price: float
    ask_price: float
    bid_time: int = 0


@dataclass(frozen=True)
class _Greek:
    event_symbol: str
    delta: float
    gamma: float
    theta: float
    vega: float
    volatility: float
