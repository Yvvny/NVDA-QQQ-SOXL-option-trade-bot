from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_bot.core.enums import OptionType
from trading_bot.core.models import OptionContract, UnderlyingQuote
from trading_bot.core.time_utils import now_new_york


class TastytradeDataError(RuntimeError):
    pass


class TastytradeSdkNotInstalledError(TastytradeDataError):
    pass


@dataclass(frozen=True)
class TastytradeMarketDataDiagnostics:
    subscribed_option_contracts: int
    received_option_quotes: int
    received_greeks: int
    required_option_quotes: int
    required_greeks: int
    market_data_incomplete: bool


@dataclass(frozen=True)
class TastytradeMarketSnapshot:
    symbol: str
    expiration: date
    dte: int
    underlying_quote: UnderlyingQuote | None
    option_contracts: tuple[OptionContract, ...]
    market_data_diagnostics: TastytradeMarketDataDiagnostics | None = None


@dataclass(frozen=True)
class TastytradeFullChainSnapshot:
    symbol: str
    collected_at: Any
    expirations: tuple[date, ...]
    underlying_quote: UnderlyingQuote | None
    option_contracts: tuple[OptionContract, ...]
    market_data_diagnostics: TastytradeMarketDataDiagnostics | None = None


class TastytradeSdkDataSource:
    def __init__(
        self,
        username: str,
        password: str,
        *,
        provider_secret: str | None = None,
        refresh_token: str | None = None,
        is_test: bool = True,
        quote_timeout_seconds: float = 8.0,
        max_contracts: int = 120,
        session_factory: Callable[..., Any] | None = None,
        option_chain_fetcher: Callable[[Any, str], Mapping[date, Sequence[Any]]] | None = None,
        streamer_factory: Callable[[Any], Any] | None = None,
        quote_class: Any | None = None,
        greeks_class: Any | None = None,
    ) -> None:
        self.source_name = "tastytrade_sdk"

        if not (username and password) and not (provider_secret and refresh_token):
            raise TastytradeDataError(
                "TASTYTRADE OAuth credentials or username/password are required for "
                "source=tastytrade."
            )
        self.username = username
        self.password = password
        self.provider_secret = provider_secret
        self.refresh_token = refresh_token
        self.is_test = is_test
        self.quote_timeout_seconds = quote_timeout_seconds
        self.max_contracts = max_contracts
        self.session_factory = session_factory
        self.option_chain_fetcher = option_chain_fetcher
        self.streamer_factory = streamer_factory
        self.quote_class = quote_class
        self.greeks_class = greeks_class

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        quote_timeout_seconds: float = 8.0,
        max_contracts: int = 120,
    ) -> TastytradeSdkDataSource:
        values = _merged_env(env)
        return cls(
            username=values.get("TASTYTRADE_USERNAME", ""),
            password=values.get("TASTYTRADE_PASSWORD", ""),
            provider_secret=(values.get("TASTYTRADE_PROVIDER_SECRET") or values.get("TT_SECRET")),
            refresh_token=(values.get("TASTYTRADE_REFRESH_TOKEN") or values.get("TT_REFRESH")),
            is_test=_parse_bool(values.get("TASTYTRADE_IS_TEST", "true")),
            quote_timeout_seconds=quote_timeout_seconds,
            max_contracts=max_contracts,
        )

    def fetch_snapshot(self, symbol: str, target_dte: int) -> TastytradeMarketSnapshot:
        return asyncio.run(self._fetch_snapshot_async(symbol, target_dte))

    def fetch_full_chain_snapshot(self, symbol: str) -> TastytradeFullChainSnapshot:
        return asyncio.run(self._fetch_full_chain_snapshot_async(symbol))

    async def _fetch_snapshot_async(
        self,
        symbol: str,
        target_dte: int,
    ) -> TastytradeMarketSnapshot:
        sdk = self._load_sdk()
        session = self._create_session(sdk["Session"])
        try:
            chain = await _await_if_needed(sdk["get_option_chain"](session, symbol.upper()))
            expiration, options = _select_expiration(chain, target_dte)
            selected_options = _limit_contract_count(options, self.max_contracts)
            streamer_symbols = [
                option.streamer_symbol
                for option in selected_options
                if getattr(option, "streamer_symbol", None)
            ]
            underlying_symbol = symbol.upper()
            quotes, greeks = await self._fetch_quotes_and_greeks(
                session=session,
                streamer_factory=sdk["DXLinkStreamer"],
                quote_class=sdk["Quote"],
                greeks_class=sdk["Greeks"],
                symbols=(underlying_symbol, *streamer_symbols),
                greek_symbols=tuple(streamer_symbols),
            )
            required_events = _required_option_event_count(len(streamer_symbols))
            received_option_quotes = _option_quote_count(
                tuple(streamer_symbols),
                quotes,
            )
            received_greeks = _greek_count(tuple(streamer_symbols), greeks)
            contracts = contracts_from_sdk_options(selected_options, quotes, greeks)
            underlying_quote = underlying_quote_from_streamer(underlying_symbol, quotes)
            return TastytradeMarketSnapshot(
                symbol=underlying_symbol,
                expiration=expiration,
                dte=max(0, (expiration - date.today()).days),
                underlying_quote=underlying_quote,
                option_contracts=tuple(contracts),
                market_data_diagnostics=TastytradeMarketDataDiagnostics(
                    subscribed_option_contracts=len(streamer_symbols),
                    received_option_quotes=received_option_quotes,
                    received_greeks=received_greeks,
                    required_option_quotes=required_events,
                    required_greeks=required_events,
                    market_data_incomplete=(
                        received_option_quotes < required_events
                        or received_greeks < required_events
                    ),
                ),
            )
        finally:
            client = getattr(session, "_client", None)
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()

    async def _fetch_full_chain_snapshot_async(
        self,
        symbol: str,
    ) -> TastytradeFullChainSnapshot:
        sdk = self._load_sdk()
        session = self._create_session(sdk["Session"])
        try:
            chain = await _await_if_needed(sdk["get_option_chain"](session, symbol.upper()))
            ordered_expirations = tuple(sorted(chain))
            all_options = [
                option
                for expiration in ordered_expirations
                for option in chain.get(expiration, ())
            ]
            streamer_symbols = [
                option.streamer_symbol
                for option in all_options
                if getattr(option, "streamer_symbol", None)
            ]
            quotes: dict[str, Any] = {}
            greeks: dict[str, Any] = {}
            underlying_symbol = symbol.upper()
            required_option_quotes = 0
            required_greeks = 0
            received_option_quotes = 0
            received_greeks = 0
            for batch_symbols in _chunk_symbols(streamer_symbols, self.max_contracts):
                batch_quotes, batch_greeks = await self._fetch_quotes_and_greeks(
                    session=session,
                    streamer_factory=sdk["DXLinkStreamer"],
                    quote_class=sdk["Quote"],
                    greeks_class=sdk["Greeks"],
                    symbols=(underlying_symbol, *batch_symbols),
                    greek_symbols=tuple(batch_symbols),
                )
                quotes.update(batch_quotes)
                greeks.update(batch_greeks)
                batch_required = _required_option_event_count(len(batch_symbols))
                required_option_quotes += batch_required
                required_greeks += batch_required
                received_option_quotes += _option_quote_count(tuple(batch_symbols), batch_quotes)
                received_greeks += _greek_count(tuple(batch_symbols), batch_greeks)

            contracts = contracts_from_sdk_options(all_options, quotes, greeks)
            underlying_quote = underlying_quote_from_streamer(underlying_symbol, quotes)
            return TastytradeFullChainSnapshot(
                symbol=underlying_symbol,
                collected_at=now_new_york(),
                expirations=ordered_expirations,
                underlying_quote=underlying_quote,
                option_contracts=tuple(contracts),
                market_data_diagnostics=TastytradeMarketDataDiagnostics(
                    subscribed_option_contracts=len(streamer_symbols),
                    received_option_quotes=received_option_quotes,
                    received_greeks=received_greeks,
                    required_option_quotes=required_option_quotes,
                    required_greeks=required_greeks,
                    market_data_incomplete=(
                        received_option_quotes < required_option_quotes
                        or received_greeks < required_greeks
                    ),
                ),
            )
        finally:
            client = getattr(session, "_client", None)
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()

    def _create_session(self, session_factory: Callable[..., Any]) -> Any:
        if self.session_factory is None and self.provider_secret and self.refresh_token:
            return session_factory(
                provider_secret=self.provider_secret,
                refresh_token=self.refresh_token,
                is_test=self.is_test,
            )
        return session_factory(self.username, self.password, is_test=self.is_test)

    def _load_sdk(self) -> dict[str, Any]:
        if (
            self.session_factory is not None
            and self.option_chain_fetcher is not None
            and self.streamer_factory is not None
            and self.quote_class is not None
            and self.greeks_class is not None
        ):
            return {
                "Session": self.session_factory,
                "get_option_chain": self.option_chain_fetcher,
                "DXLinkStreamer": self.streamer_factory,
                "Quote": self.quote_class,
                "Greeks": self.greeks_class,
            }

        try:
            from tastytrade import DXLinkStreamer, Session
            from tastytrade.dxfeed import Greeks, Quote
            from tastytrade.instruments import get_option_chain
        except ImportError as exc:
            raise TastytradeSdkNotInstalledError(
                "Install the optional dependency with: "
                '.\\.venv\\Scripts\\python.exe -m pip install -e ".[tastytrade]"'
            ) from exc

        return {
            "Session": Session,
            "get_option_chain": get_option_chain,
            "DXLinkStreamer": DXLinkStreamer,
            "Quote": Quote,
            "Greeks": Greeks,
        }

    async def _fetch_quotes_and_greeks(
        self,
        *,
        session: Any,
        streamer_factory: Callable[[Any], Any],
        quote_class: Any,
        greeks_class: Any,
        symbols: tuple[str, ...],
        greek_symbols: tuple[str, ...],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        quotes: dict[str, Any] = {}
        greeks: dict[str, Any] = {}

        async with streamer_factory(session) as streamer:
            await streamer.subscribe(quote_class, list(symbols))
            if greek_symbols:
                await streamer.subscribe(greeks_class, list(greek_symbols))
            deadline = asyncio.get_running_loop().time() + self.quote_timeout_seconds
            required_option_events = _required_option_event_count(len(greek_symbols))

            while asyncio.get_running_loop().time() < deadline:
                await _collect_event(streamer, quote_class, quotes, deadline)
                if greek_symbols:
                    await _collect_event(streamer, greeks_class, greeks, deadline)
                if _has_minimum_market_data(
                    symbols,
                    greek_symbols,
                    quotes,
                    greeks,
                    required_option_events=required_option_events,
                ):
                    break

        return quotes, greeks


def contracts_from_sdk_options(
    options: Sequence[Any],
    quotes: Mapping[str, Any],
    greeks: Mapping[str, Any],
) -> list[OptionContract]:
    contracts: list[OptionContract] = []
    for option in options:
        streamer_symbol = getattr(option, "streamer_symbol", "")
        quote = quotes.get(streamer_symbol)
        greek = greeks.get(streamer_symbol)
        bid = _optional_float(getattr(quote, "bid_price", None))
        ask = _optional_float(getattr(quote, "ask_price", None))
        if bid is None or ask is None:
            continue
        delta = _optional_float(getattr(greek, "delta", None))
        iv = _optional_float(getattr(greek, "volatility", None))
        contracts.append(
            OptionContract(
                symbol=str(option.symbol),
                underlying=str(option.underlying_symbol),
                expiration=option.expiration_date,
                strike=_float_from_decimal(option.strike_price),
                option_type=_map_option_type(option.option_type),
                bid=bid,
                ask=ask,
                mid=round((bid + ask) / 2, 4),
                delta=delta,
                gamma=_optional_float(getattr(greek, "gamma", None)),
                theta=_optional_float(getattr(greek, "theta", None)),
                vega=_optional_float(getattr(greek, "vega", None)),
                iv=iv,
                volume=_optional_int(getattr(option, "volume", None)),
                open_interest=_optional_int(getattr(option, "open_interest", None)),
                allow_missing_activity_data=True,
            )
        )
    return contracts


def underlying_quote_from_streamer(
    symbol: str,
    quotes: Mapping[str, Any],
) -> UnderlyingQuote | None:
    quote = quotes.get(symbol)
    if quote is None:
        return None
    bid = _optional_float(getattr(quote, "bid_price", None))
    ask = _optional_float(getattr(quote, "ask_price", None))
    if bid is None or ask is None:
        return None
    return UnderlyingQuote(
        symbol=symbol,
        timestamp=_timestamp_from_quote(quote),
        bid=bid,
        ask=ask,
        last=(bid + ask) / 2,
        volume=None,
    )


async def _collect_event(
    streamer: Any,
    event_class: Any,
    target: dict[str, Any],
    deadline: float,
) -> None:
    remaining = max(0.01, deadline - asyncio.get_running_loop().time())
    try:
        events = await asyncio.wait_for(streamer.get_event(event_class), timeout=remaining)
    except TimeoutError:
        return
    for event in _as_events(events):
        event_symbol = getattr(event, "event_symbol", None)
        if event_symbol:
            target[event_symbol] = event


def _has_minimum_market_data(
    symbols: tuple[str, ...],
    greek_symbols: tuple[str, ...],
    quotes: Mapping[str, Any],
    greeks: Mapping[str, Any],
    *,
    required_option_events: int | None = None,
) -> bool:
    option_symbols = [symbol for symbol in symbols if symbol in greek_symbols]
    option_quote_count = _option_quote_count(tuple(option_symbols), quotes)
    greek_count = _greek_count(greek_symbols, greeks)
    required = (
        required_option_events
        if required_option_events is not None
        else _required_option_event_count(len(greek_symbols))
    )
    return option_quote_count >= required and greek_count >= required


def _required_option_event_count(option_symbol_count: int) -> int:
    if option_symbol_count <= 0:
        return 0
    return min(30, option_symbol_count)


def _option_quote_count(
    option_symbols: tuple[str, ...],
    quotes: Mapping[str, Any],
) -> int:
    return sum(1 for symbol in option_symbols if symbol in quotes)


def _greek_count(
    greek_symbols: tuple[str, ...],
    greeks: Mapping[str, Any],
) -> int:
    return sum(1 for symbol in greek_symbols if symbol in greeks)


def _select_expiration(
    chain: Mapping[date, Sequence[Any]],
    target_dte: int,
) -> tuple[date, Sequence[Any]]:
    if not chain:
        raise TastytradeDataError("Tastytrade returned an empty option chain.")
    today = date.today()
    expiration = min(chain, key=lambda item: abs((item - today).days - target_dte))
    return expiration, chain[expiration]


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _limit_contract_count(options: Sequence[Any], max_contracts: int) -> list[Any]:
    if len(options) <= max_contracts:
        return list(options)
    ordered = sorted(options, key=lambda item: _float_from_decimal(item.strike_price))
    midpoint = len(ordered) // 2
    half = max_contracts // 2
    return ordered[max(0, midpoint - half) : midpoint + half]


def _chunk_symbols(symbols: Sequence[str], batch_size: int) -> list[tuple[str, ...]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return [
        tuple(symbols[index : index + batch_size])
        for index in range(0, len(symbols), batch_size)
    ]


def _as_events(events: Any) -> list[Any]:
    if events is None:
        return []
    if isinstance(events, list | tuple):
        return list(events)
    return [events]


def _float_from_decimal(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric:
        return None
    return numeric


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric >= 0 else None


def _map_option_type(value: Any) -> OptionType:
    raw = getattr(value, "value", value)
    normalized = str(raw).lower()
    if normalized in {"c", "call", "optiontype.call"}:
        return OptionType.CALL
    if normalized in {"p", "put", "optiontype.put"}:
        return OptionType.PUT
    raise TastytradeDataError(f"Unsupported option type from tastytrade: {value!r}")


def _timestamp_from_quote(quote: Any):
    from datetime import UTC, datetime

    raw_time = getattr(quote, "bid_time", None) or getattr(quote, "event_time", None)
    if isinstance(raw_time, int | float) and raw_time > 0:
        seconds = raw_time / 1000 if raw_time > 10_000_000_000 else raw_time
        return datetime.fromtimestamp(seconds, tz=UTC).astimezone(now_new_york().tzinfo)
    return now_new_york()


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _merged_env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    if env is not None:
        return env
    values = _read_dotenv(Path(".env"))
    values.update(os.environ)
    return values


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values
