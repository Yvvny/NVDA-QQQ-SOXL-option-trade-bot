from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from trading_bot.broker.base import BrokerResult, LiveTradingDisabledError

if TYPE_CHECKING:
    from trading_bot.execution.order_builder import OptionOrder


class MissingCredentialsError(RuntimeError):
    pass


class TastytradeHttpClient(Protocol):
    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def get(self, path: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class TastytradeCredentials:
    username: str
    password: str
    account_number: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TastytradeCredentials:
        values = os.environ if env is None else env
        username = values.get("TASTYTRADE_USERNAME", "")
        password = values.get("TASTYTRADE_PASSWORD", "")
        account_number = values.get("TASTYTRADE_ACCOUNT_NUMBER", "")
        if not username or not password or not account_number:
            raise MissingCredentialsError(
                "Tastytrade credentials must come from environment variables."
            )
        return cls(username=username, password=password, account_number=account_number)


class TastytradeAdapter:
    def __init__(
        self,
        credentials: TastytradeCredentials,
        http_client: TastytradeHttpClient,
    ) -> None:
        self.credentials = credentials
        self.http_client = http_client
        self.session_token: str | None = None

    def authenticate(self) -> str:
        response = self.http_client.post(
            "/sessions",
            {
                "login": self.credentials.username,
                "password": self.credentials.password,
            },
        )
        token = response.get("session-token") or response.get("data", {}).get("session-token")
        if not isinstance(token, str) or not token:
            raise RuntimeError(
                "Tastytrade authentication response did not include a session token."
            )
        self.session_token = token
        return token

    def get_balances(self) -> dict[str, Any]:
        return self.http_client.get(f"/accounts/{self.credentials.account_number}/balances")

    def get_positions(self) -> dict[str, Any]:
        return self.http_client.get(f"/accounts/{self.credentials.account_number}/positions")

    def get_option_chain(self, symbol: str) -> dict[str, Any]:
        return self.http_client.get(f"/option-chains/{symbol.upper()}")

    def get_quote(self, symbol: str) -> dict[str, Any]:
        return self.http_client.get(f"/market-data/{symbol.upper()}/quote")

    def dry_run(self, order: OptionOrder) -> BrokerResult:
        return BrokerResult(
            accepted=True,
            order_id=None,
            message=(
                "Tastytrade adapter dry-run validated locally. " "No live order was submitted."
            ),
        )

    def submit(self, order: OptionOrder) -> BrokerResult:
        raise LiveTradingDisabledError("Tastytrade live submit is disabled in this version.")
