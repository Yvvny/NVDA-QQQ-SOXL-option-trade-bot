from datetime import date

import pytest

from trading_bot.broker import (
    LiveTradingDisabledError,
    MissingCredentialsError,
    TastytradeAdapter,
    TastytradeCredentials,
)
from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import OptionContract, OptionLeg, StrategyCandidate
from trading_bot.execution import OrderBuilder


def test_tastytrade_credentials_load_from_environment_mapping():
    credentials = TastytradeCredentials.from_env(
        {
            "TASTYTRADE_USERNAME": "user",
            "TASTYTRADE_PASSWORD": "pass",
            "TASTYTRADE_ACCOUNT_NUMBER": "acct",
        }
    )

    assert credentials.username == "user"
    assert credentials.account_number == "acct"


def test_tastytrade_credentials_fail_safely_when_missing():
    with pytest.raises(MissingCredentialsError):
        TastytradeCredentials.from_env({})


def test_tastytrade_adapter_auth_and_fetches_use_mocked_client():
    client = _FakeHttpClient()
    adapter = TastytradeAdapter(
        TastytradeCredentials(username="user", password="pass", account_number="acct"),
        client,
    )

    assert adapter.authenticate() == "token"
    assert adapter.get_balances()["path"] == "/accounts/acct/balances"
    assert adapter.get_positions()["path"] == "/accounts/acct/positions"
    assert adapter.get_option_chain("qqq")["path"] == "/option-chains/QQQ"
    assert adapter.get_quote("qqq")["path"] == "/market-data/QQQ/quote"


def test_tastytrade_adapter_dry_run_only_and_submit_disabled():
    adapter = TastytradeAdapter(
        TastytradeCredentials(username="user", password="pass", account_number="acct"),
        _FakeHttpClient(),
    )
    order = OrderBuilder().build(_candidate())

    result = adapter.dry_run(order)
    assert result.accepted is True
    assert result.order_id is None

    with pytest.raises(LiveTradingDisabledError):
        adapter.submit(order)


class _FakeHttpClient:
    def post(self, path, payload):
        return {"session-token": "token", "path": path, "payload": payload}

    def get(self, path):
        return {"path": path}


def _candidate() -> StrategyCandidate:
    expiration = date(2026, 6, 19)
    short_put = OptionContract(
        symbol="QQQ 2026-06-19 450 put",
        underlying="QQQ",
        expiration=expiration,
        strike=450,
        option_type=OptionType.PUT,
        bid=0.45,
        ask=0.55,
        mid=0.50,
    )
    long_put = OptionContract(
        symbol="QQQ 2026-06-19 449 put",
        underlying="QQQ",
        expiration=expiration,
        strike=449,
        option_type=OptionType.PUT,
        bid=0.20,
        ask=0.30,
        mid=0.25,
    )
    return StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="QQQ",
        legs=(
            OptionLeg(short_put, OptionAction.SELL),
            OptionLeg(long_put, OptionAction.BUY),
        ),
        dte=30,
        entry_score=75,
        max_profit=25,
        max_loss=75,
        expected_credit_or_debit=25,
        reason_codes=("fixture",),
        exit_plan=None,
    )
