from dataclasses import dataclass
from decimal import Decimal

import pytest

from trading_bot.broker.tastytrade_account import (
    TastytradeAccountCredentials,
    TastytradeAccountCredentialsError,
    TastytradeAccountDataSource,
)


def test_tastytrade_account_credentials_load_oauth_env_names():
    credentials = TastytradeAccountCredentials.from_env(
        {
            "TASTYTRADE_PROVIDER_SECRET": "secret",
            "TASTYTRADE_REFRESH_TOKEN": "refresh",
            "TASTYTRADE_ACCOUNT_NUMBER": "5WT12345",
            "TASTYTRADE_IS_TEST": "false",
        }
    )

    assert credentials.provider_secret == "secret"
    assert credentials.refresh_token == "refresh"
    assert credentials.account_number == "5WT12345"
    assert credentials.is_test is False


def test_tastytrade_account_credentials_load_sdk_env_aliases():
    credentials = TastytradeAccountCredentials.from_env(
        {
            "TT_SECRET": "secret",
            "TT_REFRESH": "refresh",
        }
    )

    assert credentials.provider_secret == "secret"
    assert credentials.refresh_token == "refresh"
    assert credentials.account_number is None
    assert credentials.is_test is True


def test_tastytrade_account_credentials_fail_safely_when_oauth_values_missing():
    with pytest.raises(TastytradeAccountCredentialsError):
        TastytradeAccountCredentials.from_env({})


def test_tastytrade_account_source_fetches_read_only_snapshot():
    source = TastytradeAccountDataSource(
        TastytradeAccountCredentials(
            provider_secret="secret",
            refresh_token="refresh",
            account_number="5WT12345",
        ),
        session_factory=_FakeSession,
        account_class=_FakeAccount,
    )

    snapshot = source.fetch_snapshot()

    assert snapshot.connected is True
    assert snapshot.account_number is None
    assert snapshot.account_number_masked == "****2345"
    assert snapshot.balances["net_liquidating_value"] == 2000.5
    assert snapshot.balances["account_number"] == "****2345"
    assert snapshot.positions[0]["symbol"] == "QQQ   260619C00510000"
    assert snapshot.trading_status["is_closing_only"] is False
    assert _FakeSession.closed is True


class _FakeSession:
    closed = False

    def __init__(self, provider_secret, refresh_token, is_test):
        self.provider_secret = provider_secret
        self.refresh_token = refresh_token
        self.is_test = is_test
        self._client = _FakeClient()


class _FakeClient:
    async def aclose(self):
        _FakeSession.closed = True


class _FakeAccount:
    account_number = "5WT12345"
    account_type_name = "Individual"
    margin_or_cash = "margin"
    day_trader_status = False

    @classmethod
    async def get(cls, session, account_number=None, include_closed=False):
        assert account_number == "5WT12345"
        return cls()

    async def get_balances(self, session):
        return _FakeModel(
            {
                "account_number": "5WT12345",
                "net_liquidating_value": Decimal("2000.50"),
                "cash_balance": Decimal("500.25"),
                "derivative_buying_power": Decimal("1200"),
                "maintenance_requirement": Decimal("100"),
            }
        )

    async def get_positions(self, session, include_closed=False, include_marks=False):
        assert include_closed is False
        assert include_marks is True
        return [
            _FakeModel(
                {
                    "symbol": "QQQ   260619C00510000",
                    "instrument_type": "Equity Option",
                    "quantity": Decimal("1"),
                    "quantity_direction": "Long",
                    "average_open_price": Decimal("1.20"),
                    "mark": Decimal("1.35"),
                    "realized_day_gain": Decimal("15"),
                }
            )
        ]

    async def get_trading_status(self, session):
        return _FakeModel(
            {
                "is_closing_only": False,
                "is_frozen": False,
                "options_level": "Covered and Defined Risk",
            }
        )


@dataclass
class _FakeModel:
    payload: dict

    def model_dump(self, mode="json"):
        return self.payload
