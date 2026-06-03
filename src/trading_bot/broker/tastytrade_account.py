from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_bot.core.time_utils import iso_now_new_york

class TastytradeAccountError(RuntimeError):
    pass


class TastytradeAccountCredentialsError(TastytradeAccountError):
    pass


@dataclass(frozen=True)
class TastytradeAccountCredentials:
    provider_secret: str
    refresh_token: str
    account_number: str | None
    is_test: bool = True

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> TastytradeAccountCredentials:
        values = _merged_env(env)
        provider_secret = values.get("TASTYTRADE_PROVIDER_SECRET") or values.get("TT_SECRET") or ""
        refresh_token = values.get("TASTYTRADE_REFRESH_TOKEN") or values.get("TT_REFRESH") or ""
        account_number = values.get("TASTYTRADE_ACCOUNT_NUMBER") or None
        is_test = _parse_bool(values.get("TASTYTRADE_IS_TEST", "true"))

        if not provider_secret or not refresh_token:
            raise TastytradeAccountCredentialsError(
                "Read-only account view requires TASTYTRADE_PROVIDER_SECRET/TT_SECRET "
                "and TASTYTRADE_REFRESH_TOKEN/TT_REFRESH in .env or the shell environment."
            )
        return cls(
            provider_secret=provider_secret,
            refresh_token=refresh_token,
            account_number=account_number,
            is_test=is_test,
        )


@dataclass(frozen=True)
class TastytradeAccountSnapshot:
    connected: bool
    source: str
    is_test: bool
    fetched_at: str
    account_number: str | None = None
    account_number_masked: str | None = None
    account_type_name: str | None = None
    margin_or_cash: str | None = None
    day_trader_status: str | bool | None = None
    balances: dict[str, Any] | None = None
    positions: list[dict[str, Any]] | None = None
    trading_status: dict[str, Any] | None = None
    error_type: str | None = None
    message: str | None = None

    @classmethod
    def disconnected(
        cls,
        error: Exception,
        *,
        is_test: bool = True,
    ) -> TastytradeAccountSnapshot:
        return cls(
            connected=False,
            source="tastytrade",
            is_test=is_test,
            fetched_at=iso_now_new_york(),
            error_type=error.__class__.__name__,
            message=str(error),
            balances={},
            positions=[],
            trading_status={},
        )


class TastytradeAccountDataSource:
    def __init__(
        self,
        credentials: TastytradeAccountCredentials,
        *,
        session_factory: Callable[..., Any] | None = None,
        account_class: Any | None = None,
    ) -> None:
        self.credentials = credentials
        self.session_factory = session_factory
        self.account_class = account_class

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> TastytradeAccountDataSource:
        return cls(TastytradeAccountCredentials.from_env(env))

    def fetch_snapshot(self) -> TastytradeAccountSnapshot:
        return asyncio.run(self._fetch_snapshot())

    async def _fetch_snapshot(self) -> TastytradeAccountSnapshot:
        session_cls, account_cls = self._load_sdk()
        session = session_cls(
            provider_secret=self.credentials.provider_secret,
            refresh_token=self.credentials.refresh_token,
            is_test=self.credentials.is_test,
        )
        try:
            account = await self._fetch_account(session, account_cls)
            balances = await account.get_balances(session)
            positions = await account.get_positions(
                session,
                include_closed=False,
                include_marks=True,
            )
            trading_status = await account.get_trading_status(session)
            account_number = str(account.account_number)
            return TastytradeAccountSnapshot(
                connected=True,
                source="tastytrade",
                is_test=self.credentials.is_test,
                fetched_at=iso_now_new_york(),
                account_number=None,
                account_number_masked=_mask_account_number(account_number),
                account_type_name=_optional_str(getattr(account, "account_type_name", None)),
                margin_or_cash=_optional_str(getattr(account, "margin_or_cash", None)),
                day_trader_status=getattr(account, "day_trader_status", None),
                balances=_model_to_dict(balances),
                positions=[_model_to_dict(position) for position in positions],
                trading_status=_model_to_dict(trading_status),
            )
        finally:
            client = getattr(session, "_client", None)
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()

    async def _fetch_account(self, session: Any, account_cls: Any) -> Any:
        account_number = self.credentials.account_number
        if account_number:
            return await account_cls.get(session, account_number=account_number)

        accounts = await account_cls.get(session)
        if not accounts:
            raise TastytradeAccountError("No open tastytrade accounts were returned.")
        if not isinstance(accounts, list):
            return accounts
        return accounts[0]

    def _load_sdk(self) -> tuple[Callable[..., Any], Any]:
        if self.session_factory is not None and self.account_class is not None:
            return self.session_factory, self.account_class
        try:
            from tastytrade import Account, Session
        except ImportError as exc:
            raise TastytradeAccountError(
                "Install the optional dependency with: "
                '.\\.venv\\Scripts\\python.exe -m pip install -e ".[tastytrade]"'
            ) from exc
        return Session, Account


def fetch_tastytrade_account_snapshot() -> TastytradeAccountSnapshot:
    try:
        return TastytradeAccountDataSource.from_env().fetch_snapshot()
    except Exception as exc:  # noqa: BLE001 - UI needs a safe local connection status.
        return TastytradeAccountSnapshot.disconnected(
            exc,
            is_test=_parse_bool(_merged_env(None).get("TASTYTRADE_IS_TEST", "true")),
        )


def _model_to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        raw = value.model_dump(mode="json")
    elif hasattr(value, "dict"):
        raw = value.dict()
    elif hasattr(value, "__dict__"):
        raw = dict(value.__dict__)
    else:
        raw = {"value": value}
    return _clean_value(raw)


def _clean_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text == "account_number" and item is not None:
                cleaned[key_text] = _mask_account_number(str(item))
            else:
                cleaned[key_text] = _clean_value(item)
        return cleaned
    if isinstance(value, list | tuple):
        return [_clean_value(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _mask_account_number(account_number: str) -> str:
    if len(account_number) <= 4:
        return "*" * len(account_number)
    return f"{'*' * (len(account_number) - 4)}{account_number[-4:]}"


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _merged_env(env: Mapping[str, str] | None) -> dict[str, str]:
    if env is not None:
        return dict(env)
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


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
