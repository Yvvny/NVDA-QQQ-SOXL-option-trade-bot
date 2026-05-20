from __future__ import annotations

# ruff: noqa: E501
import base64
import hmac
import json
import os
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from trading_bot.broker import fetch_tastytrade_account_snapshot
from trading_bot.config.settings import load_settings
from trading_bot.paper import DEFAULT_PAPER_STATE_PATH, PaperTradingSimulator
from trading_bot.runner import DryRunBotRunner

DEFAULT_PAPER_AUDIT_LOG_PATH = "docs/reports/paper_audit.jsonl"
DEFAULT_BOT_TRADE_JOURNAL_PATH = "docs/reports/bot_trade_journal.jsonl"
NEW_YORK_TIME_ZONE = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class UiServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    audit_log_path: str = "docs/reports/trade_audit.jsonl"


def run_ui_server(config: UiServerConfig | None = None) -> None:
    config = config or UiServerConfig()
    server = build_ui_server(config)
    try:
        print(f"Trading bot UI running at http://{config.host}:{config.port}")
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping trading bot UI.")
    finally:
        server.server_close()


def build_ui_server(config: UiServerConfig | None = None) -> ThreadingHTTPServer:
    config = config or UiServerConfig()

    class TradingBotUiHandler(_TradingBotUiHandler):
        server_config = config

    return ThreadingHTTPServer((config.host, config.port), TradingBotUiHandler)


class _TradingBotUiHandler(BaseHTTPRequestHandler):
    server_config = UiServerConfig()

    def do_GET(self) -> None:
        if not self._require_auth():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(_HTML)
            return
        if parsed.path == "/api/health":
            self._send_json({"ok": True, "mode": load_settings().risk.default_mode})
            return
        if parsed.path == "/api/status":
            self._send_json(_status_payload(self.server_config.audit_log_path))
            return
        if parsed.path == "/api/config":
            self._send_json(asdict(load_settings()))
            return
        if parsed.path == "/api/account":
            self._send_json(fetch_tastytrade_account_snapshot())
            return
        if parsed.path == "/api/account-view":
            query = parse_qs(parsed.query)
            account_type = query.get("type", ["paper"])[0]
            self._send_json(_account_view_payload(account_type))
            return
        if parsed.path == "/api/paper":
            self._send_json(
                PaperTradingSimulator(state_path=DEFAULT_PAPER_STATE_PATH).load_state().to_summary()
            )
            return
        if parsed.path == "/api/paper-runtime":
            self._send_json(_paper_runtime_payload())
            return
        if parsed.path == "/api/logs":
            query = parse_qs(parsed.query)
            log_type = query.get("type", ["paper"])[0]
            limit = _int_from_query(query, "limit", default=50, minimum=1, maximum=500)
            path = (
                DEFAULT_PAPER_AUDIT_LOG_PATH
                if log_type == "paper"
                else self.server_config.audit_log_path
            )
            records = (
                _read_recent_paper_logs(path, limit)
                if log_type == "paper"
                else _read_recent_jsonl(path, limit)
            )
            self._send_json(
                {
                    "type": log_type,
                    "path": path,
                    "timezone": "America/New_York",
                    "records": records,
                }
            )
            return
        if parsed.path == "/api/audit":
            query = parse_qs(parsed.query)
            limit = _int_from_query(query, "limit", default=20, minimum=1, maximum=100)
            self._send_json(
                {
                    "records": _read_recent_jsonl(self.server_config.audit_log_path, limit),
                    "path": self.server_config.audit_log_path,
                }
            )
            return
        self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._require_auth():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/run-once":
            payload = self._read_json_body()
            try:
                result = _run_once_from_payload(payload, self.server_config.audit_log_path)
            except Exception as exc:  # noqa: BLE001 - surface safe local error details.
                self._send_json(
                    {"error": exc.__class__.__name__, "message": str(exc)},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            self._send_json(result.to_dict())
            return
        self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(_jsonable(payload), sort_keys=True).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _require_auth(self) -> bool:
        credentials = _ui_auth_credentials()
        if credentials is None:
            return True
        if _authorization_matches(self.headers.get("Authorization"), credentials):
            return True
        self.send_response(HTTPStatus.UNAUTHORIZED.value)
        self.send_header("WWW-Authenticate", 'Basic realm="Trading Bot Control"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        body = b"Authentication required."
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False


def _run_once_from_payload(payload: dict[str, Any], audit_log_path: str):
    source = str(payload.get("source", "mock"))
    symbol = str(payload.get("symbol", "QQQ")).upper()
    target_dte = int(payload.get("target_dte", 30))
    max_candidates = int(payload.get("max_candidates", 1))
    quote_timeout_seconds = float(payload.get("quote_timeout_seconds", 8.0))
    max_contracts = int(payload.get("max_contracts", 120))

    runner = DryRunBotRunner(
        settings=load_settings(),
        source=source,
        audit_log_path=audit_log_path,
        max_candidates_per_cycle=max_candidates,
        symbol=symbol,
        target_dte=target_dte,
        quote_timeout_seconds=quote_timeout_seconds,
        max_contracts=max_contracts,
    )
    return runner.run_once()


def _ui_auth_credentials() -> tuple[str, str] | None:
    values = _merged_env()
    username = values.get("UI_AUTH_USERNAME", "").strip()
    password = values.get("UI_AUTH_PASSWORD", "")
    if not username and not password:
        return None
    if not username or not password:
        return ("", "")
    return (username, password)


def _authorization_matches(
    authorization_header: str | None,
    credentials: tuple[str, str],
) -> bool:
    username, password = credentials
    if not username or not password:
        return False
    if not authorization_header or not authorization_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(authorization_header.removeprefix("Basic ")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    expected = f"{username}:{password}"
    return hmac.compare_digest(decoded, expected)


def _merged_env() -> dict[str, str]:
    values = _read_dotenv(Path(".env"))
    values.update(os.environ)
    return values


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _status_payload(audit_log_path: str) -> dict[str, Any]:
    settings = load_settings()
    audit_path = Path(audit_log_path)
    recent_records = _read_recent_jsonl(audit_path, 5)
    return {
        "mode": settings.risk.default_mode,
        "account_equity": settings.account.assumed_equity,
        "live_trading_default_allowed": settings.forbidden.allow_live_trading_default,
        "allow_0dte": settings.forbidden.allow_0dte,
        "allow_naked_options": settings.forbidden.allow_naked_options,
        "allow_market_orders_options": settings.forbidden.allow_market_orders_options,
        "audit_log_path": str(audit_path),
        "audit_log_exists": audit_path.exists(),
        "recent_audit_count": len(recent_records),
        "latest_status": recent_records[-1].get("status") if recent_records else None,
        "server_time": datetime.now(UTC).isoformat(),
        "server_time_new_york": _ny_time_string(datetime.now(UTC).isoformat()),
    }


def _account_view_payload(account_type: str) -> dict[str, Any]:
    if account_type == "live":
        return _live_account_view_payload()
    return _paper_account_view_payload()


def _paper_account_view_payload() -> dict[str, Any]:
    state = PaperTradingSimulator(state_path=DEFAULT_PAPER_STATE_PATH).load_state()
    summary = state.to_summary()
    runtime = _paper_runtime_payload()
    return {
        "account_type": "paper",
        "title": "Paper Account",
        "connected": True,
        "message": runtime["message"],
        "updated_at": summary["updated_at"],
        "updated_at_new_york": _ny_time_string(summary["updated_at"]),
        "runtime": runtime,
        "metrics": {
            "equity": summary["equity"],
            "cash": summary["equity"],
            "buying_power": max(0.0, summary["equity"] - summary["total_open_max_loss"]),
            "reserved_risk": summary["total_open_max_loss"],
            "realized_pnl": summary["realized_pnl"],
            "unrealized_pnl": summary["unrealized_pnl"],
            "total_pnl": summary["total_pnl"],
            "return_pct": summary["total_return_pct"],
            "open_positions": summary["open_positions"],
            "closed_trades": summary["closed_trades"],
        },
        "positions": [
            {
                "id": position.position_id,
                "symbol": position.underlying,
                "instrument_type": "Bot Paper Position",
                "bot_managed": True,
                "managed_by": "Bot",
                "strategy": position.strategy_name,
                "quantity": 1,
                "direction": position.price_effect,
                "average_open_price": position.expected_credit_or_debit,
                "mark_price": None,
                "mark_value": position.last_mark_value,
                "day_pnl": position.unrealized_pnl,
                "max_loss": position.max_loss,
                "entry_score": position.entry_score,
                "opened_at": position.opened_at,
                "exit_plan": position.exit_plan,
                "reason_codes": [],
                "legs": [asdict(leg) for leg in position.legs],
                "raw": asdict(position),
            }
            for position in state.open_positions
        ],
        "closed_trades": [
            {
                "symbol": trade.position.underlying,
                "strategy": trade.position.strategy_name,
                "closed_at": trade.closed_at,
                "exit_reason": trade.exit_reason,
                "realized_pnl": trade.realized_pnl,
            }
            for trade in state.closed_trades[-20:]
        ],
        "details": {
            **summary,
            "updated_at_new_york": _ny_time_string(summary["updated_at"]),
            "runtime": runtime,
        },
        "logs": _read_recent_paper_logs(DEFAULT_PAPER_AUDIT_LOG_PATH, 20),
    }


def _live_account_view_payload() -> dict[str, Any]:
    snapshot = fetch_tastytrade_account_snapshot()
    balances = snapshot.balances or {}
    bot_metadata = _bot_position_metadata_index()
    return {
        "account_type": "live",
        "title": "Actual Account",
        "connected": snapshot.connected,
        "message": snapshot.message
        or f"{snapshot.account_type_name or ''} {snapshot.margin_or_cash or ''}".strip(),
        "updated_at": snapshot.fetched_at,
        "metrics": {
            "equity": balances.get("net_liquidating_value"),
            "cash": balances.get("cash_balance"),
            "buying_power": balances.get("derivative_buying_power"),
            "reserved_risk": balances.get("used_derivative_buying_power"),
            "realized_pnl": None,
            "unrealized_pnl": None,
            "total_pnl": None,
            "return_pct": None,
            "open_positions": len(snapshot.positions or []),
            "closed_trades": None,
        },
        "positions": [
            _live_position_view(position, bot_metadata.get(str(position.get("symbol") or "")))
            for position in (snapshot.positions or [])
        ],
        "closed_trades": [],
        "details": {
            "account": {
                "source": snapshot.source,
                "is_test": snapshot.is_test,
                "connected": snapshot.connected,
                "account_number_masked": snapshot.account_number_masked,
                "account_type_name": snapshot.account_type_name,
                "margin_or_cash": snapshot.margin_or_cash,
                "day_trader_status": snapshot.day_trader_status,
                "fetched_at": snapshot.fetched_at,
                "fetched_at_new_york": _ny_time_string(snapshot.fetched_at),
                "message": snapshot.message,
                "error_type": snapshot.error_type,
            },
            "balances": balances,
            "positions": snapshot.positions or [],
            "trading_status": snapshot.trading_status or {},
            "bot_metadata_journal": DEFAULT_BOT_TRADE_JOURNAL_PATH,
        },
        "logs": [],
    }


def _live_position_view(
    position: dict[str, Any], metadata: dict[str, Any] | None
) -> dict[str, Any]:
    bot_managed = metadata is not None
    metadata = metadata or {}
    strategy = metadata.get("strategy_name") or metadata.get("strategy") or "Manual / External"
    return {
        "id": position.get("symbol"),
        "symbol": position.get("symbol"),
        "instrument_type": position.get("instrument_type"),
        "bot_managed": bot_managed,
        "managed_by": "Bot" if bot_managed else "Manual / External",
        "strategy": strategy,
        "quantity": position.get("quantity"),
        "direction": position.get("quantity_direction"),
        "average_open_price": position.get("average_open_price"),
        "mark_price": position.get("mark_price"),
        "mark_value": position.get("mark"),
        "day_pnl": (
            position.get("realized_day_gain")
            if position.get("realized_day_gain") is not None
            else position.get("unrealized_day_gain")
        ),
        "max_loss": metadata.get("max_loss"),
        "entry_score": metadata.get("entry_score"),
        "opened_at": metadata.get("opened_at") or position.get("created_at"),
        "exit_plan": metadata.get("exit_plan") or {},
        "reason_codes": metadata.get("reason_codes") or [],
        "legs": metadata.get("legs") or [],
        "raw": position,
    }


def _bot_position_metadata_index() -> dict[str, dict[str, Any]]:
    records = _read_recent_jsonl(DEFAULT_BOT_TRADE_JOURNAL_PATH, 1000)
    index: dict[str, dict[str, Any]] = {}
    for record in records:
        metadata = _bot_position_metadata(record)
        for symbol in _bot_position_symbols(record):
            index[symbol] = metadata
    return index


def _bot_position_metadata(record: dict[str, Any]) -> dict[str, Any]:
    candidate = _first_dict(
        record.get("candidate"),
        _nested_dict(record, "trade_record", "candidate"),
        _nested_dict(record, "order", "candidate"),
    )
    position = _first_dict(record.get("position"), record.get("paper_position"))
    source = candidate or position or record
    return {
        "strategy_name": source.get("strategy_name") or record.get("strategy_name"),
        "underlying": source.get("underlying") or record.get("underlying") or record.get("symbol"),
        "entry_score": source.get("entry_score") or record.get("entry_score"),
        "max_loss": source.get("max_loss") or record.get("max_loss"),
        "max_profit": source.get("max_profit") or record.get("max_profit"),
        "exit_plan": source.get("exit_plan") or record.get("exit_plan") or {},
        "reason_codes": source.get("reason_codes") or record.get("reason_codes") or [],
        "legs": source.get("legs") or record.get("legs") or [],
        "opened_at": record.get("opened_at") or record.get("logged_at"),
        "journal_event": record.get("event_type"),
    }


def _bot_position_symbols(record: dict[str, Any]) -> set[str]:
    symbols = {
        str(value)
        for value in (
            record.get("broker_position_symbol"),
            record.get("position_symbol"),
            record.get("option_symbol"),
        )
        if value
    }
    for container in (
        record,
        record.get("candidate"),
        record.get("position"),
        record.get("paper_position"),
        _nested_dict(record, "trade_record", "candidate"),
        _nested_dict(record, "order", "candidate"),
    ):
        if isinstance(container, dict):
            symbol = container.get("symbol")
            if symbol:
                symbols.add(str(symbol))
            for leg in container.get("legs") or []:
                if not isinstance(leg, dict):
                    continue
                leg_symbol = leg.get("symbol")
                contract = leg.get("contract") if isinstance(leg.get("contract"), dict) else {}
                contract_symbol = contract.get("symbol")
                if leg_symbol:
                    symbols.add(str(leg_symbol))
                if contract_symbol:
                    symbols.add(str(contract_symbol))
    return symbols


def _nested_dict(payload: dict[str, Any], *keys: str) -> dict[str, Any] | None:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if isinstance(value, dict) else None


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _read_recent_jsonl(path: str | Path, limit: int) -> list[dict[str, Any]]:
    audit_path = Path(path)
    if not audit_path.exists():
        return []
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = {"raw": line, "parse_error": True}
        if isinstance(payload, dict):
            if payload.get("logged_at"):
                payload["logged_at_new_york"] = _ny_time_string(str(payload["logged_at"]))
            records.append(payload)
    return records


def _read_recent_paper_logs(path: str | Path, limit: int) -> list[dict[str, Any]]:
    records = _read_recent_jsonl(path, 1000)
    relevant: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for record in records:
        pending.append(record)
        if record.get("event_type") != "paper_cycle":
            continue
        result = _nested_dict(record, "result")
        state_path = result.get("state_path") if result else None
        if _same_state_path(state_path, DEFAULT_PAPER_STATE_PATH):
            relevant.extend(pending)
        pending = []
    return relevant[-limit:]


def _paper_runtime_payload() -> dict[str, Any]:
    records = _read_recent_paper_logs(DEFAULT_PAPER_AUDIT_LOG_PATH, 50)
    cycles = [record for record in records if record.get("event_type") == "paper_cycle"]
    latest = cycles[-1] if cycles else None
    result = _nested_dict(latest, "result") if latest else None
    logged_at = str(latest.get("logged_at")) if latest and latest.get("logged_at") else None
    seconds_since = _seconds_since(logged_at)
    interval_seconds = 300
    is_recent = seconds_since is not None and seconds_since <= interval_seconds * 1.75
    status = "running" if is_recent else "stale_or_stopped"
    if latest is None:
        status = "no_cycles_yet"

    generated = result.get("generated_candidates") if result else None
    opened = result.get("opened_positions") if result else None
    rejected = result.get("rejected_candidates") if result else None
    errors = result.get("errors") if result else []
    message = "Paper runner is scanning every 5 minutes."
    if status == "stale_or_stopped":
        message = "Paper runner has not written a recent cycle; it may be stopped."
    if status == "no_cycles_yet":
        message = "Paper runner has not written any cycle yet."
    if generated == 0 and status == "running":
        message = "Paper runner is running, but no strategy-qualified candidates were generated."

    return {
        "status": status,
        "is_running_recently": is_recent,
        "last_cycle_at": logged_at,
        "last_cycle_at_new_york": _ny_time_string(logged_at),
        "seconds_since_last_cycle": seconds_since,
        "expected_interval_seconds": interval_seconds,
        "next_cycle_estimate_new_york": _next_cycle_time_string(logged_at, interval_seconds),
        "cycle_index": result.get("cycle_index") if result else None,
        "source": result.get("source") if result else None,
        "symbols": result.get("symbols") if result else [],
        "strict_spec": result.get("strict_spec") if result else None,
        "generated_candidates": generated,
        "opened_positions": opened,
        "rejected_candidates": rejected,
        "errors": errors or [],
        "message": message,
    }


def _same_state_path(value: Any, expected: str | Path) -> bool:
    if not value:
        return False
    normalized_value = str(value).replace("\\", "/").lower()
    normalized_expected = Path(expected).as_posix().lower()
    return normalized_value.endswith(normalized_expected)


def _seconds_since(value: str | None) -> int | None:
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        return None
    return max(0, int((datetime.now(UTC) - timestamp.astimezone(UTC)).total_seconds()))


def _next_cycle_time_string(value: str | None, interval_seconds: int) -> str | None:
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        return None
    return (
        (timestamp.astimezone(UTC) + timedelta(seconds=interval_seconds))
        .astimezone(NEW_YORK_TIME_ZONE)
        .strftime("%Y-%m-%d %H:%M:%S %Z")
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp


def _ny_time_string(value: str | None) -> str | None:
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        return value
    return timestamp.astimezone(NEW_YORK_TIME_ZONE).strftime("%Y-%m-%d %H:%M:%S %Z")


def _int_from_query(
    query: dict[str, list[str]],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = query.get(key, [str(default)])[0]
    value = int(raw)
    return min(max(value, minimum), maximum)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trading Bot Control</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0f1216;
      --panel: #171c22;
      --panel-2: #1f2630;
      --border: #303945;
      --text: #edf2f7;
      --muted: #a8b3c2;
      --good: #45c486;
      --warn: #f2b84b;
      --bad: #ef6b6b;
      --accent: #6bb5ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 22px;
      border-bottom: 1px solid var(--border);
      background: #12171d;
    }
    h1 { margin: 0; font-size: 18px; font-weight: 650; }
    main {
      display: grid;
      grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
      gap: 18px;
      padding: 18px;
      max-width: 1440px;
      margin: 0 auto;
    }
    section {
      border: 1px solid var(--border);
      background: var(--panel);
      border-radius: 8px;
      padding: 14px;
    }
    h2 {
      margin: 0 0 12px;
      font-size: 14px;
      font-weight: 650;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .stack { display: grid; gap: 14px; }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 12px;
    }
    .metric {
      min-height: 76px;
      border: 1px solid var(--border);
      background: var(--panel-2);
      border-radius: 8px;
      padding: 12px;
    }
    .label { color: var(--muted); font-size: 12px; }
    .value { margin-top: 8px; font-size: 21px; font-weight: 700; }
    .good { color: var(--good); }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    form { display: grid; gap: 11px; }
    label { display: grid; gap: 5px; color: var(--muted); font-size: 12px; }
    input, select {
      width: 100%;
      border: 1px solid var(--border);
      background: #101419;
      color: var(--text);
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
    }
    button {
      border: 1px solid #3478b9;
      background: #15568e;
      color: white;
      border-radius: 6px;
      padding: 10px 12px;
      font-weight: 650;
      cursor: pointer;
    }
    button.secondary {
      border-color: var(--border);
      background: var(--panel-2);
    }
    button:disabled { opacity: 0.55; cursor: progress; }
    .toolbar { display: flex; gap: 8px; align-items: center; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .account-status {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin-bottom: 12px;
    }
    .small-table th, .small-table td { font-size: 12px; }
    pre {
      margin: 0;
      overflow: auto;
      max-height: 520px;
      background: #0b0e12;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      color: #d8e2ee;
      line-height: 1.45;
      font-size: 12px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      text-align: left;
      border-bottom: 1px solid var(--border);
      padding: 9px 8px;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th { color: var(--muted); font-weight: 600; }
    .pill {
      display: inline-flex;
      align-items: center;
      height: 24px;
      border-radius: 999px;
      border: 1px solid var(--border);
      padding: 0 10px;
      color: var(--muted);
      background: var(--panel-2);
    }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; padding: 12px; }
      .grid { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
      header { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Trading Bot Control</h1>
    <div class="toolbar">
      <span id="mode-pill" class="pill">mode</span>
      <button class="secondary" id="refresh">Refresh</button>
    </div>
  </header>
  <main>
    <div class="stack">
      <section>
        <h2>Run Control</h2>
        <form id="run-form">
          <div class="row">
            <label>Source
              <select name="source">
                <option value="mock">mock</option>
                <option value="tastytrade">tastytrade</option>
              </select>
            </label>
            <label>Symbol
              <input name="symbol" value="QQQ" autocomplete="off">
            </label>
          </div>
          <div class="row">
            <label>Target DTE
              <input name="target_dte" type="number" min="1" value="30">
            </label>
            <label>Max Candidates
              <input name="max_candidates" type="number" min="1" value="1">
            </label>
          </div>
          <button id="run-once" type="submit">Run Dry-Run</button>
        </form>
      </section>
      <section>
        <h2>Safety</h2>
        <div id="safety" class="stack"></div>
      </section>
      <section>
        <h2>Account</h2>
        <div class="account-status">
          <span id="account-connection" class="pill">not loaded</span>
          <span id="account-number" class="pill">account</span>
          <button class="secondary" id="refresh-account" type="button">Refresh Account</button>
        </div>
        <div id="account-message" class="label"></div>
      </section>
    </div>
    <div class="stack">
      <section>
        <h2>Status</h2>
        <div class="grid">
          <div class="metric">
            <div class="label">Mode</div><div id="mode" class="value"></div>
          </div>
          <div class="metric">
            <div class="label">Equity</div><div id="equity" class="value"></div>
          </div>
          <div class="metric">
            <div class="label">Latest</div><div id="latest" class="value"></div>
          </div>
          <div class="metric">
            <div class="label">Audit Rows</div><div id="audit-count" class="value"></div>
          </div>
        </div>
      </section>
      <section>
        <h2>Account Balances</h2>
        <div class="grid">
          <div class="metric">
            <div class="label">Net Liq</div><div id="net-liq" class="value"></div>
          </div>
          <div class="metric">
            <div class="label">Cash</div><div id="cash-balance" class="value"></div>
          </div>
          <div class="metric">
            <div class="label">Derivative BP</div><div id="derivative-bp" class="value"></div>
          </div>
          <div class="metric">
            <div class="label">Maintenance</div><div id="maintenance-req" class="value"></div>
          </div>
        </div>
      </section>
      <section>
        <h2>Paper Account</h2>
        <div class="grid">
          <div class="metric">
            <div class="label">Virtual Equity</div><div id="paper-equity" class="value"></div>
          </div>
          <div class="metric">
            <div class="label">Total P/L</div><div id="paper-pnl" class="value"></div>
          </div>
          <div class="metric">
            <div class="label">Return</div><div id="paper-return" class="value"></div>
          </div>
          <div class="metric">
            <div class="label">Open Paper</div><div id="paper-open" class="value"></div>
          </div>
        </div>
      </section>
      <section>
        <h2>Positions</h2>
        <table class="small-table">
          <thead>
            <tr>
              <th>Symbol</th><th>Type</th><th>Qty</th><th>Avg</th><th>Mark</th><th>Day P/L</th>
            </tr>
          </thead>
          <tbody id="positions-body"></tbody>
        </table>
      </section>
      <section>
        <h2>Trading Status</h2>
        <pre id="trading-status">{}</pre>
      </section>
      <section>
        <h2>Last Run</h2>
        <pre id="run-output">{}</pre>
      </section>
      <section>
        <h2>Recent Audit</h2>
        <table>
          <thead><tr><th>Time</th><th>Status</th><th>Strategy</th><th>Risk</th></tr></thead>
          <tbody id="audit-body"></tbody>
        </table>
      </section>
    </div>
  </main>
  <script>
    const statusUrl = "/api/status";
    const accountUrl = "/api/account";
    const paperUrl = "/api/paper";
    const auditUrl = "/api/audit?limit=12";
    const runUrl = "/api/run-once";

    function text(id, value) {
      document.getElementById(id).textContent = value ?? "";
    }

    function safetyRow(name, value) {
      const cls = value ? "bad" : "good";
      return `<div><span class="${cls}">${value ? "blocked" : "safe"}</span> ${name}</div>`;
    }

    function money(value) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return "";
      return "$" + numeric.toLocaleString(undefined, {maximumFractionDigits: 2});
    }

    async function loadStatus() {
      const status = await fetch(statusUrl).then(r => r.json());
      text("mode", status.mode);
      text("equity", "$" + Number(status.account_equity).toFixed(0));
      text("latest", status.latest_status || "none");
      text("audit-count", status.recent_audit_count);
      text("mode-pill", status.mode);
      document.getElementById("safety").innerHTML = [
        safetyRow("live default", status.live_trading_default_allowed),
        safetyRow("0DTE", status.allow_0dte),
        safetyRow("naked options", status.allow_naked_options),
        safetyRow("market orders", status.allow_market_orders_options)
      ].join("");
      await loadAudit();
      await loadPaper();
    }

    async function loadPaper() {
      const paper = await fetch(paperUrl).then(r => r.json());
      text("paper-equity", money(paper.equity));
      text("paper-pnl", money(paper.total_pnl));
      text("paper-return", `${paper.total_return_pct ?? 0}%`);
      text("paper-open", paper.open_positions ?? 0);
    }

    async function loadAccount() {
      const account = await fetch(accountUrl).then(r => r.json());
      const connection = document.getElementById("account-connection");
      connection.textContent = account.connected ? "connected" : "not connected";
      connection.className = "pill " + (account.connected ? "good" : "warn");
      text("account-number", account.account_number_masked || "account");
      text("account-message", account.connected
        ? `${account.account_type_name || ""} ${account.margin_or_cash || ""}`.trim()
        : `${account.error_type || "AccountError"}: ${account.message || ""}`);

      const balances = account.balances || {};
      text("net-liq", money(balances.net_liquidating_value));
      text("cash-balance", money(balances.cash_balance));
      text("derivative-bp", money(balances.derivative_buying_power));
      text("maintenance-req", money(balances.maintenance_requirement));

      const positionRows = (account.positions || []).map(position => {
        return `<tr>
          <td>${position.symbol || ""}</td>
          <td>${position.instrument_type || ""}</td>
          <td>${position.quantity || ""} ${position.quantity_direction || ""}</td>
          <td>${money(position.average_open_price)}</td>
          <td>${money(position.mark || position.mark_price)}</td>
          <td>${money(position.realized_day_gain)}</td>
        </tr>`;
      });
      document.getElementById("positions-body").innerHTML = positionRows.join("")
        || '<tr><td colspan="6">No open positions loaded.</td></tr>';
      document.getElementById("trading-status").textContent = JSON.stringify(
        account.trading_status || {},
        null,
        2
      );
    }

    async function loadAudit() {
      const payload = await fetch(auditUrl).then(r => r.json());
      const rows = payload.records.map(record => {
        const risk = record.risk_decision || {};
        const candidate = record.candidate || {};
        return `<tr>
          <td>${record.logged_at || ""}</td>
          <td>${record.status || ""}</td>
          <td>${candidate.strategy_name || ""}</td>
          <td>${(risk.reason_codes || []).join(", ")}</td>
        </tr>`;
      });
      document.getElementById("audit-body").innerHTML = rows.join("");
    }

    document.getElementById("refresh").addEventListener("click", loadStatus);
    document.getElementById("refresh-account").addEventListener("click", loadAccount);
    document.getElementById("run-form").addEventListener("submit", async event => {
      event.preventDefault();
      const button = document.getElementById("run-once");
      button.disabled = true;
      const form = new FormData(event.target);
      const payload = {
        source: form.get("source"),
        symbol: form.get("symbol"),
        target_dte: Number(form.get("target_dte")),
        max_candidates: Number(form.get("max_candidates"))
      };
      try {
        const result = await fetch(runUrl, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        }).then(r => r.json());
        document.getElementById("run-output").textContent = JSON.stringify(result, null, 2);
        await loadStatus();
      } finally {
        button.disabled = false;
      }
    });
    loadStatus();
    loadAccount();
  </script>
</body>
</html>
"""

_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trading Bot Control</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0f1216;
      --panel: #171c22;
      --panel-2: #1f2630;
      --border: #303945;
      --text: #edf2f7;
      --muted: #a8b3c2;
      --good: #45c486;
      --warn: #f2b84b;
      --bad: #ef6b6b;
      --accent: #6bb5ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 22px;
      border-bottom: 1px solid var(--border);
      background: #12171d;
      position: sticky;
      top: 0;
      z-index: 5;
    }
    h1 { margin: 0; font-size: 18px; font-weight: 650; }
    h2 {
      margin: 0 0 12px;
      font-size: 14px;
      font-weight: 650;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0;
    }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 14px;
      padding: 16px;
      max-width: 1320px;
      margin: 0 auto;
    }
    section {
      border: 1px solid var(--border);
      background: var(--panel);
      border-radius: 8px;
      padding: 14px;
    }
    .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .toolbar select, input {
      border: 1px solid var(--border);
      background: #101419;
      color: var(--text);
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
    }
    button {
      border: 1px solid #3478b9;
      background: #15568e;
      color: white;
      border-radius: 6px;
      padding: 9px 11px;
      font-weight: 650;
      cursor: pointer;
    }
    button.secondary {
      border-color: var(--border);
      background: var(--panel-2);
    }
    button:disabled { opacity: 0.55; cursor: progress; }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 999px;
      border: 1px solid var(--border);
      padding: 0 10px;
      color: var(--muted);
      background: var(--panel-2);
      white-space: nowrap;
    }
    .good { color: var(--good); }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    .account-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }
    .account-title { font-size: 20px; font-weight: 700; margin-bottom: 4px; }
    .muted { color: var(--muted); }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(150px, 1fr));
      gap: 12px;
    }
    .metric {
      min-height: 78px;
      border: 1px solid var(--border);
      background: var(--panel-2);
      border-radius: 8px;
      padding: 12px;
    }
    .label { color: var(--muted); font-size: 12px; }
    .value { margin-top: 8px; font-size: 21px; font-weight: 700; overflow-wrap: anywhere; }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      text-align: left;
      border-bottom: 1px solid var(--border);
      padding: 9px 8px;
      vertical-align: top;
      overflow-wrap: anywhere;
      font-size: 12px;
    }
    th { color: var(--muted); font-weight: 600; }
    pre {
      margin: 0;
      overflow: auto;
      max-height: 520px;
      background: #0b0e12;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      color: #d8e2ee;
      line-height: 1.45;
      font-size: 12px;
    }
    details summary {
      cursor: pointer;
      color: var(--muted);
      font-weight: 650;
      margin-bottom: 10px;
    }
    .diagnostic-form {
      display: grid;
      grid-template-columns: repeat(5, minmax(120px, 1fr));
      gap: 10px;
      align-items: end;
    }
    .diagnostic-form label { display: grid; gap: 5px; color: var(--muted); font-size: 12px; }
    .diagnostic-form input, .diagnostic-form select { width: 100%; }
    @media (max-width: 900px) {
      header { align-items: flex-start; flex-direction: column; }
      .grid { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
      .diagnostic-form { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Trading Bot Control</h1>
      <div class="muted">Same layout for paper and actual account. Actual account is read-only.</div>
    </div>
    <div class="toolbar">
      <span id="mode-pill" class="pill">dry_run</span>
      <select id="account-select" title="Account view">
        <option value="paper">Paper Account</option>
        <option value="live">Actual Account</option>
      </select>
      <button id="refresh">Refresh</button>
    </div>
  </header>
  <main>
    <section>
      <div class="account-header">
        <div>
          <div id="account-title" class="account-title">Paper Account</div>
          <div id="account-message" class="muted"></div>
        </div>
        <div class="toolbar">
          <span id="connection-pill" class="pill">not loaded</span>
          <button class="secondary" id="show-details">Detailed Info</button>
          <button class="secondary" id="show-logs">Logs</button>
        </div>
      </div>
    </section>

    <section>
      <h2>Paper Runner</h2>
      <div class="grid">
        <div class="metric"><div class="label">Runner Status</div><div id="runtime-status" class="value"></div></div>
        <div class="metric"><div class="label">Last Cycle NY</div><div id="runtime-last-cycle" class="value"></div></div>
        <div class="metric"><div class="label">Next Cycle Estimate</div><div id="runtime-next-cycle" class="value"></div></div>
        <div class="metric"><div class="label">Last Scan Result</div><div id="runtime-last-result" class="value"></div></div>
      </div>
    </section>

    <section>
      <h2>Account Overview</h2>
      <div class="grid">
        <div class="metric"><div class="label">Equity / Net Liq</div><div id="metric-equity" class="value"></div></div>
        <div class="metric"><div class="label">Cash</div><div id="metric-cash" class="value"></div></div>
        <div class="metric"><div class="label">Buying Power</div><div id="metric-buying-power" class="value"></div></div>
        <div class="metric"><div class="label">Reserved Risk / Used BP</div><div id="metric-reserved-risk" class="value"></div></div>
        <div class="metric"><div class="label">Realized P/L</div><div id="metric-realized" class="value"></div></div>
        <div class="metric"><div class="label">Unrealized P/L</div><div id="metric-unrealized" class="value"></div></div>
        <div class="metric"><div class="label">Total P/L</div><div id="metric-total-pnl" class="value"></div></div>
        <div class="metric"><div class="label">Return</div><div id="metric-return" class="value"></div></div>
      </div>
    </section>

    <section>
      <h2>Positions</h2>
      <table>
        <thead>
          <tr>
            <th>Symbol</th><th>Managed By</th><th>Type</th><th>Strategy</th><th>Qty</th><th>Direction</th>
            <th>Avg Price</th><th>Mark Price</th><th>Mark Value</th><th>P/L</th><th>Risk / Score</th>
          </tr>
        </thead>
        <tbody id="positions-body"></tbody>
      </table>
    </section>

    <section>
      <h2>Closed Trades</h2>
      <table>
        <thead><tr><th>Symbol</th><th>Strategy</th><th>Closed</th><th>Reason</th><th>Realized P/L</th></tr></thead>
        <tbody id="closed-body"></tbody>
      </table>
    </section>

    <section id="details-section">
      <h2>Detailed Info</h2>
      <pre id="details-output">{}</pre>
    </section>

    <section id="logs-section">
      <h2>Recent Logs</h2>
      <table>
        <thead><tr><th>Time</th><th>Event</th><th>Status / Summary</th></tr></thead>
        <tbody id="logs-body"></tbody>
      </table>
      <pre id="logs-output" style="margin-top: 12px;">[]</pre>
    </section>

    <section>
      <details>
        <summary>Diagnostic Dry-Run Tool</summary>
        <form id="run-form" class="diagnostic-form">
          <label>Source
            <select name="source">
              <option value="mock">mock</option>
              <option value="tastytrade">tastytrade</option>
            </select>
          </label>
          <label>Symbol
            <input name="symbol" value="QQQ" autocomplete="off">
          </label>
          <label>Target DTE
            <input name="target_dte" type="number" min="1" value="30">
          </label>
          <label>Max Candidates
            <input name="max_candidates" type="number" min="1" value="1">
          </label>
          <button id="run-once" type="submit">Run Diagnostic</button>
        </form>
        <pre id="run-output" style="margin-top: 12px;">{}</pre>
      </details>
    </section>
  </main>
  <script>
    const statusUrl = "/api/status";
    const accountViewUrl = "/api/account-view";
    const logsUrl = "/api/logs";
    const runUrl = "/api/run-once";
    let currentAccount = "paper";
    let currentView = null;

    function text(id, value) {
      document.getElementById(id).textContent = value ?? "";
    }

    function money(value) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return "N/A";
      return "$" + numeric.toLocaleString(undefined, {maximumFractionDigits: 2});
    }

    function pct(value) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return "N/A";
      return numeric.toLocaleString(undefined, {maximumFractionDigits: 2}) + "%";
    }

    function plain(value) {
      return value === null || value === undefined || value === "" ? "N/A" : value;
    }

    function timeNy(value) {
      if (!value) return "N/A";
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return value;
      return parsed.toLocaleString("en-US", {
        timeZone: "America/New_York",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
        timeZoneName: "short"
      });
    }

    function safe(value) {
      return String(plain(value))
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    async function loadStatus() {
      const status = await fetch(statusUrl).then(r => r.json());
      text("mode-pill", status.mode);
    }

    async function loadAccountView() {
      currentAccount = document.getElementById("account-select").value;
      const view = await fetch(`${accountViewUrl}?type=${currentAccount}`).then(r => r.json());
      currentView = view;
      renderAccountView(view);
      await loadLogs();
      await loadStatus();
    }

    function renderAccountView(view) {
      const metrics = view.metrics || {};
      const runtime = view.runtime || {};
      text("account-title", view.title || "Account");
      text("account-message", view.message || "");
      const pill = document.getElementById("connection-pill");
      pill.textContent = view.connected ? "connected" : "not connected";
      pill.className = "pill " + (view.connected ? "good" : "warn");

      text("runtime-status", runtime.status || "N/A");
      text("runtime-last-cycle", runtime.last_cycle_at_new_york || "N/A");
      text("runtime-next-cycle", runtime.next_cycle_estimate_new_york || "N/A");
      text("runtime-last-result", [
        runtime.generated_candidates === undefined || runtime.generated_candidates === null ? "" : `generated ${runtime.generated_candidates}`,
        runtime.opened_positions === undefined || runtime.opened_positions === null ? "" : `opened ${runtime.opened_positions}`,
        runtime.rejected_candidates === undefined || runtime.rejected_candidates === null ? "" : `rejected ${runtime.rejected_candidates}`
      ].filter(Boolean).join(" | ") || "N/A");

      text("metric-equity", money(metrics.equity));
      text("metric-cash", money(metrics.cash));
      text("metric-buying-power", money(metrics.buying_power));
      text("metric-reserved-risk", money(metrics.reserved_risk));
      text("metric-realized", money(metrics.realized_pnl));
      text("metric-unrealized", money(metrics.unrealized_pnl));
      text("metric-total-pnl", money(metrics.total_pnl));
      text("metric-return", pct(metrics.return_pct));

      const positionRows = (view.positions || []).map(position => {
        const riskScore = [
          position.max_loss === null || position.max_loss === undefined ? "" : `risk ${money(position.max_loss)}`,
          position.entry_score === null || position.entry_score === undefined ? "" : `score ${position.entry_score}`
        ].filter(Boolean).join(" / ");
        return `<tr>
          <td>${safe(position.symbol)}</td>
          <td>${safe(position.managed_by)}</td>
          <td>${safe(position.instrument_type)}</td>
          <td>${safe(position.strategy)}</td>
          <td>${safe(position.quantity)}</td>
          <td>${safe(position.direction)}</td>
          <td>${money(position.average_open_price)}</td>
          <td>${money(position.mark_price)}</td>
          <td>${money(position.mark_value)}</td>
          <td>${money(position.day_pnl)}</td>
          <td>${riskScore || "N/A"}</td>
        </tr>`;
      });
      document.getElementById("positions-body").innerHTML = positionRows.join("")
        || '<tr><td colspan="11">No positions for this account view.</td></tr>';

      const closedRows = (view.closed_trades || []).map(trade => {
        return `<tr>
          <td>${safe(trade.symbol)}</td>
          <td>${safe(trade.strategy)}</td>
          <td>${safe(trade.closed_at)}</td>
          <td>${safe(trade.exit_reason)}</td>
          <td>${money(trade.realized_pnl)}</td>
        </tr>`;
      });
      document.getElementById("closed-body").innerHTML = closedRows.join("")
        || '<tr><td colspan="5">No closed trades in this account view.</td></tr>';

      document.getElementById("details-output").textContent = JSON.stringify(
        view.details || {},
        null,
        2
      );
    }

    async function loadLogs() {
      const payload = await fetch(`${logsUrl}?type=${currentAccount === "paper" ? "paper" : "dry"}&limit=50`)
        .then(r => r.json());
      const rows = (payload.records || []).map(record => {
        const result = record.result || {};
        const candidate = record.candidate || {};
        const summary = result.summary || {};
        const shortSummary = [
          record.status,
          candidate.strategy_name,
          result.opened_positions === undefined ? "" : `opened ${result.opened_positions}`,
          result.rejected_candidates === undefined ? "" : `rejected ${result.rejected_candidates}`,
          summary.equity === undefined ? "" : `equity ${money(summary.equity)}`
        ].filter(Boolean).join(" | ");
        return `<tr>
          <td>${safe(record.logged_at_new_york || timeNy(record.logged_at))}</td>
          <td>${safe(record.event_type)}</td>
          <td>${safe(shortSummary || "see raw log below")}</td>
        </tr>`;
      });
      document.getElementById("logs-body").innerHTML = rows.join("")
        || '<tr><td colspan="3">No logs yet.</td></tr>';
      document.getElementById("logs-output").textContent = JSON.stringify(
        payload.records || [],
        null,
        2
      );
    }

    document.getElementById("account-select").addEventListener("change", loadAccountView);
    document.getElementById("refresh").addEventListener("click", loadAccountView);
    document.getElementById("show-details").addEventListener("click", () => {
      document.getElementById("details-section").scrollIntoView({behavior: "smooth"});
    });
    document.getElementById("show-logs").addEventListener("click", () => {
      document.getElementById("logs-section").scrollIntoView({behavior: "smooth"});
    });
    document.getElementById("run-form").addEventListener("submit", async event => {
      event.preventDefault();
      const button = document.getElementById("run-once");
      button.disabled = true;
      const form = new FormData(event.target);
      const payload = {
        source: form.get("source"),
        symbol: form.get("symbol"),
        target_dte: Number(form.get("target_dte")),
        max_candidates: Number(form.get("max_candidates"))
      };
      try {
        const result = await fetch(runUrl, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        }).then(r => r.json());
        document.getElementById("run-output").textContent = JSON.stringify(result, null, 2);
        await loadAccountView();
      } finally {
        button.disabled = false;
      }
    });
    loadAccountView();
  </script>
</body>
</html>
"""
