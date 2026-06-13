from __future__ import annotations

# ruff: noqa: E501
import base64
import hmac
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from hashlib import sha256
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from trading_bot.broker import fetch_tastytrade_account_snapshot
from trading_bot.config.settings import load_settings
from trading_bot.core.time_utils import (
    NEW_YORK_TIME_ZONE,
    format_new_york_timestamp,
    now_new_york,
    parse_timestamp,
)
from trading_bot.paper import DEFAULT_PAPER_STATE_PATH, PaperTradingSimulator
from trading_bot.research_bot import (
    StrategyChatAssistant,
    StrategyChatMessage,
)
from trading_bot.research_bot.openai_client import DEFAULT_RESEARCH_MODEL
from trading_bot.runner import DryRunBotRunner

DEFAULT_PAPER_AUDIT_LOG_PATH = "docs/reports/paper_audit.jsonl"
DEFAULT_BOT_TRADE_JOURNAL_PATH = "docs/reports/bot_trade_journal.jsonl"
DEFAULT_LIVE_EQUITY_HISTORY_PATH = "docs/reports/live_account_equity.jsonl"
DEFAULT_RESEARCH_QUEUE_PATH = "docs/reports/research_queue.jsonl"
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
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(_HTML)
            return
        if parsed.path == "/api/session":
            self._send_json({"authenticated": self._is_authenticated()})
            return
        if not self._require_auth():
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
            range_key = query.get("range", ["all"])[0]
            ledger_filter = query.get("ledger_filter", ["all"])[0]
            self._send_json(
                _account_view_payload(
                    account_type,
                    range_key=range_key,
                    ledger_filter=ledger_filter,
                )
            )
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
        if parsed.path == "/api/research-queue":
            query = parse_qs(parsed.query)
            limit = _int_from_query(query, "limit", default=100, minimum=1, maximum=500)
            self._send_json(_research_queue_payload(limit=limit))
            return
        self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/login":
            payload = self._read_json_body()
            session_cookie = self._login(payload)
            if session_cookie is None:
                self._send_json(
                    {"error": "unauthorized", "message": "Invalid UI credentials."},
                    status=HTTPStatus.UNAUTHORIZED,
                )
                return
            self._send_json({"ok": True}, extra_headers={"Set-Cookie": session_cookie})
            return
        if parsed.path == "/api/logout":
            self._send_json({"ok": True}, extra_headers={"Set-Cookie": self._clear_session()})
            return
        if not self._require_auth():
            return
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
        if parsed.path == "/api/assistant":
            payload = self._read_json_body()
            try:
                result = _assistant_chat_from_payload(payload, self.server_config.audit_log_path)
            except Exception as exc:  # noqa: BLE001 - surface safe local error details.
                self._send_json(
                    {"error": exc.__class__.__name__, "message": str(exc)},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            self._send_json(result)
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

    def _send_json(
        self,
        payload: Any,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(_jsonable(payload), sort_keys=True).encode("utf-8")
        self.send_response(status.value)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _is_authenticated(self) -> bool:
        credentials = _ui_auth_credentials()
        if credentials is None:
            return True
        if _session_matches(self.headers.get("Cookie"), credentials):
            return True
        return _authorization_matches(self.headers.get("Authorization"), credentials)

    def _require_auth(self) -> bool:
        if self._is_authenticated():
            return True
        self.send_response(HTTPStatus.UNAUTHORIZED.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        body = b'{"error":"unauthorized","message":"Authentication required."}'
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def _login(self, payload: dict[str, Any]) -> str | None:
        credentials = _ui_auth_credentials()
        if credentials is None:
            return None
        username = str(payload.get("username", ""))
        password = str(payload.get("password", ""))
        if not _credentials_match(username, password, credentials):
            return None
        return self._set_session(credentials)

    def _set_session(self, credentials: tuple[str, str]) -> str:
        cookie = SimpleCookie()
        cookie["tb_ui_session"] = _session_cookie_value(credentials)
        cookie["tb_ui_session"]["httponly"] = True
        cookie["tb_ui_session"]["path"] = "/"
        cookie["tb_ui_session"]["samesite"] = "Lax"
        cookie["tb_ui_session"]["max-age"] = 86400
        return cookie.output(header="").strip()

    def _clear_session(self) -> str:
        cookie = SimpleCookie()
        cookie["tb_ui_session"] = ""
        cookie["tb_ui_session"]["httponly"] = True
        cookie["tb_ui_session"]["path"] = "/"
        cookie["tb_ui_session"]["samesite"] = "Lax"
        cookie["tb_ui_session"]["max-age"] = 0
        return cookie.output(header="").strip()


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


def _assistant_chat_from_payload(payload: dict[str, Any], audit_log_path: str) -> dict[str, Any]:
    messages_payload = payload.get("messages", [])
    if not isinstance(messages_payload, list):
        raise ValueError("messages must be a list.")
    messages = tuple(
        StrategyChatMessage(
            role=str(item.get("role", "user")),
            content=str(item.get("content", "")),
        )
        for item in messages_payload
        if isinstance(item, dict) and str(item.get("content", "")).strip()
    )
    if not messages:
        raise ValueError("messages must include at least one non-empty message.")

    mode = str(payload.get("mode", "strategy")).strip() or "strategy"
    account_type = str(payload.get("account_type", "paper")).strip() or "paper"
    range_key = str(payload.get("range", "all")).strip() or "all"
    ledger_filter = str(payload.get("ledger_filter", "all")).strip() or "all"
    assistant = _build_strategy_chat_assistant()
    context = {
        "status": _status_payload(audit_log_path),
        "account_view": _account_view_payload(
            account_type,
            range_key=range_key,
            ledger_filter=ledger_filter,
        ),
        "recent_paper_logs": _read_recent_paper_logs(DEFAULT_PAPER_AUDIT_LOG_PATH, 25),
    }
    response = assistant.respond(messages, context=context, mode=mode)
    queue_item = _append_research_queue_item(
        response=response,
        assistant_model=assistant.model,
        mode=mode,
        user_message=messages[-1].content,
        account_type=account_type,
    )
    return {
        "model": assistant.model,
        "generated_at": response.generated_at.isoformat() if response.generated_at else None,
        "queued_task": queue_item,
        "response": response.to_dict(),
        "research_only": response.research_only,
        "assistant_reply": response.assistant_reply,
        "summary": response.summary,
        "needs_human_approval": response.needs_human_approval,
        "codex_task": response.codex_task,
        "proposed_changes": [asdict(item) for item in response.proposed_changes],
        "follow_up_questions": list(response.follow_up_questions),
        "confidence": response.confidence,
    }


def _build_strategy_chat_assistant() -> StrategyChatAssistant:
    return StrategyChatAssistant.from_env()


def _append_research_queue_item(
    *,
    response: Any,
    assistant_model: str,
    mode: str,
    user_message: str,
    account_type: str,
    queue_path: str | Path | None = None,
) -> dict[str, Any] | None:
    codex_task = str(getattr(response, "codex_task", "") or "").strip()
    proposed_changes = tuple(getattr(response, "proposed_changes", ()) or ())
    if not codex_task and not proposed_changes:
        return None

    created_at = now_new_york()
    item = {
        "queue_id": f"rq_{created_at.strftime('%Y%m%d%H%M%S%f')}",
        "created_at": created_at.isoformat(),
        "created_at_new_york": _ny_time_string(created_at.isoformat()),
        "status": "pending_review",
        "source": "assistant",
        "assistant_model": assistant_model,
        "mode": mode,
        "account_type": account_type,
        "user_message": user_message,
        "summary": getattr(response, "summary", ""),
        "assistant_reply": getattr(response, "assistant_reply", ""),
        "codex_task": codex_task,
        "needs_human_approval": bool(getattr(response, "needs_human_approval", True)),
        "confidence": float(getattr(response, "confidence", 0.0) or 0.0),
        "proposed_changes": [asdict(item) for item in proposed_changes],
        "follow_up_questions": list(getattr(response, "follow_up_questions", ()) or ()),
    }
    path = Path(queue_path or DEFAULT_RESEARCH_QUEUE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_jsonable(item), sort_keys=True) + "\n")
    return item


def _research_queue_payload(
    *,
    limit: int = 100,
    queue_path: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(queue_path or DEFAULT_RESEARCH_QUEUE_PATH)
    items = _read_recent_jsonl(path, limit)
    items = list(reversed(items))
    status_counts = Counter(str(item.get("status", "unknown")) for item in items)
    return {
        "path": str(path),
        "count": len(items),
        "status_counts": dict(status_counts),
        "items": items,
    }


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


def _credentials_match(
    username: str,
    password: str,
    credentials: tuple[str, str],
) -> bool:
    expected_username, expected_password = credentials
    return hmac.compare_digest(username, expected_username) and hmac.compare_digest(
        password,
        expected_password,
    )


def _session_cookie_value(credentials: tuple[str, str]) -> str:
    username, password = credentials
    signature = hmac.new(
        password.encode("utf-8"),
        username.encode("utf-8"),
        sha256,
    ).hexdigest()
    payload = f"{username}:{signature}".encode()
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _session_matches(cookie_header: str | None, credentials: tuple[str, str]) -> bool:
    if not cookie_header:
        return False
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    morsel = cookie.get("tb_ui_session")
    if morsel is None or not morsel.value:
        return False
    expected = _session_cookie_value(credentials)
    return hmac.compare_digest(morsel.value, expected)


def _merged_env() -> dict[str, str]:
    values = _read_dotenv(Path(".env"))
    values.update(os.environ)
    return values


def _research_model_default() -> str:
    values = _merged_env()
    return values.get("OPENAI_RESEARCH_MODEL", DEFAULT_RESEARCH_MODEL)


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
        "research_model_default": _research_model_default(),
        "server_time": now_new_york().isoformat(),
        "server_time_new_york": _ny_time_string(now_new_york().isoformat()),
    }


def _account_view_payload(
    account_type: str,
    *,
    range_key: str = "all",
    ledger_filter: str = "all",
) -> dict[str, Any]:
    if account_type == "live":
        return _live_account_view_payload(range_key=range_key, ledger_filter=ledger_filter)
    return _paper_account_view_payload(range_key=range_key, ledger_filter=ledger_filter)


def _paper_account_view_payload(
    *,
    range_key: str = "all",
    ledger_filter: str = "all",
) -> dict[str, Any]:
    state = PaperTradingSimulator(state_path=DEFAULT_PAPER_STATE_PATH).load_state()
    summary = state.to_summary()
    runtime = _paper_runtime_payload()
    paper_logs = _read_recent_paper_logs(DEFAULT_PAPER_AUDIT_LOG_PATH, 400)
    performance_logs = _read_paper_performance_logs(DEFAULT_PAPER_AUDIT_LOG_PATH)
    analytics = _paper_analytics_payload(paper_logs, summary)
    performance = _paper_performance_payload(performance_logs, state, range_key=range_key)
    ledger = _filter_ledger_entries(_paper_ledger_payload(paper_logs), ledger_filter)
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
            "cash": summary.get("available_cash", summary["equity"]),
            "buying_power": summary.get(
                "available_cash",
                max(0.0, summary["equity"] - summary["total_open_max_loss"]),
            ),
            "reserved_risk": summary["total_open_max_loss"],
            "realized_pnl": summary["realized_pnl"],
            "unrealized_pnl": summary["unrealized_pnl"],
            "total_pnl": summary["total_pnl"],
            "return_pct": summary["total_return_pct"],
            "open_positions": summary["open_positions"],
            "closed_trades": summary["closed_trades"],
        },
        "performance": performance,
        "analytics": analytics,
        "ledger": ledger,
        "positions": [
            _paper_position_view(position)
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
            "analytics": analytics,
        },
        "logs": paper_logs[-20:],
    }


def _paper_position_view(position) -> dict[str, Any]:
    payload = {
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
                "max_profit": position.max_profit,
                "entry_score": position.entry_score,
                "opened_at": position.opened_at,
                "exit_plan": position.exit_plan,
                "reason_codes": [],
                "legs": [asdict(leg) for leg in position.legs],
                "raw": asdict(position),
            }
    payload["structure_summary"] = _position_structure_summary(payload.get("legs") or [])
    payload["exit_monitor"] = _position_exit_monitor(payload)
    return payload


def _live_account_view_payload(
    *,
    range_key: str = "all",
    ledger_filter: str = "all",
) -> dict[str, Any]:
    snapshot = fetch_tastytrade_account_snapshot()
    balances = snapshot.balances or {}
    bot_metadata = _bot_position_metadata_index()
    _append_live_equity_snapshot(snapshot)
    performance = _live_performance_payload(snapshot, range_key=range_key)
    ledger = _filter_ledger_entries(_live_ledger_payload(), ledger_filter)
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
        "performance": performance,
        "analytics": {
            "top_spec_rejections": [],
            "top_risk_rejections": [],
            "top_liquidity_blocks": [],
        },
        "ledger": ledger,
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
    payload = {
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
        "max_profit": metadata.get("max_profit"),
        "entry_score": metadata.get("entry_score"),
        "opened_at": metadata.get("opened_at") or position.get("created_at"),
        "exit_plan": metadata.get("exit_plan") or {},
        "reason_codes": metadata.get("reason_codes") or [],
        "legs": metadata.get("legs") or [],
        "raw": position,
    }
    payload["structure_summary"] = _position_structure_summary(payload.get("legs") or [])
    payload["exit_monitor"] = _position_exit_monitor(payload)
    return payload


def _position_exit_monitor(position: dict[str, Any]) -> dict[str, Any]:
    exit_plan = position.get("exit_plan") or {}
    if not isinstance(exit_plan, dict):
        exit_plan = {}

    price_effect = str(position.get("direction") or "").lower()
    average_open_price = _float_or_none(position.get("average_open_price"))
    mark_value = _float_or_none(position.get("mark_value"))
    mark_price = _float_or_none(position.get("mark_price"))
    current_close_value = abs(mark_value) if mark_value is not None else None
    if current_close_value is None and mark_price is not None:
        current_close_value = abs(mark_price)

    entry_value = _signed_entry_value(position, average_open_price, price_effect)
    max_profit = _float_or_none(position.get("max_profit"))
    if max_profit is None:
        max_profit = _float_or_none(position.get("raw", {}).get("max_profit") if isinstance(position.get("raw"), dict) else None)
    max_loss = _float_or_none(position.get("max_loss"))

    target_close_value = None
    target_pnl = None
    profit_target_pct = _float_or_none(exit_plan.get("profit_target_pct"))
    if entry_value is not None and max_profit not in {None, 0.0} and profit_target_pct is not None:
        target_pnl = round(max_profit * profit_target_pct, 2)
        target_close_value = abs(round(entry_value + target_pnl, 2))

    stop_close_value = None
    stop_pnl = None
    stop_reason = None
    stop_loss_pct = _float_or_none(exit_plan.get("stop_loss_pct"))
    stop_loss_multiple = _float_or_none(exit_plan.get("stop_loss_multiple"))
    if entry_value is not None and max_loss not in {None, 0.0} and stop_loss_pct is not None:
        stop_pnl = round(-(max_loss * stop_loss_pct), 2)
        stop_close_value = abs(round(entry_value + stop_pnl, 2))
        stop_reason = "stop_loss"
    elif entry_value is not None and average_open_price is not None and stop_loss_multiple is not None:
        stop_pnl = round(-(abs(average_open_price) * stop_loss_multiple), 2)
        stop_close_value = abs(round(entry_value + stop_pnl, 2))
        stop_reason = "stop_loss_multiple"

    opened_at = str(position.get("opened_at") or "") or None
    earliest_expiration = _earliest_expiration(position.get("legs") or [])
    days_to_expiration = _days_until_date(earliest_expiration)
    time_exit_dte = _float_or_none(exit_plan.get("time_exit_dte"))
    days_until_time_exit = None
    time_exit_active = None
    if days_to_expiration is not None and time_exit_dte is not None:
        days_until_time_exit = int(days_to_expiration - time_exit_dte)
        time_exit_active = days_to_expiration <= time_exit_dte

    pnl = _float_or_none(position.get("day_pnl"))
    return {
        "current_close_value": round(current_close_value, 2) if current_close_value is not None else None,
        "target_close_value": target_close_value,
        "target_pnl": target_pnl,
        "stop_close_value": stop_close_value,
        "stop_pnl": stop_pnl,
        "stop_reason": stop_reason,
        "days_to_expiration": days_to_expiration,
        "time_exit_dte": int(time_exit_dte) if time_exit_dte is not None else None,
        "days_until_time_exit": days_until_time_exit,
        "time_exit_active": time_exit_active,
        "opened_at_new_york": _ny_time_string(opened_at),
        "earliest_expiration": earliest_expiration,
        "current_pnl": pnl,
        "distance_to_target_pnl": round(target_pnl - pnl, 2) if target_pnl is not None and pnl is not None else None,
        "distance_to_stop_pnl": round(pnl - stop_pnl, 2) if stop_pnl is not None and pnl is not None else None,
    }


def _signed_entry_value(position: dict[str, Any], average_open_price: float | None, price_effect: str) -> float | None:
    raw = position.get("raw")
    if isinstance(raw, dict):
        raw_entry_value = _float_or_none(raw.get("entry_value"))
        if raw_entry_value is not None:
            return raw_entry_value
    if average_open_price is None:
        return None
    return -abs(average_open_price) if price_effect == "credit" else abs(average_open_price)


def _earliest_expiration(legs: list[dict[str, Any]]) -> str | None:
    expirations = [str(leg.get("expiration")) for leg in legs if isinstance(leg, dict) and leg.get("expiration")]
    if not expirations:
        return None
    return min(expirations)


def _position_structure_summary(legs: list[dict[str, Any]]) -> str | None:
    parsed_legs: list[tuple[str, str, float, str]] = []
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        expiration = str(leg.get("expiration") or "")
        option_type = str(leg.get("option_type") or "").upper()
        strike = _float_or_none(leg.get("strike"))
        action = str(leg.get("action") or "").lower()
        if not expiration or not option_type or strike is None:
            continue
        sign = "+" if action == "buy" else "-"
        parsed_legs.append((expiration, option_type[:1], strike, sign))
    if not parsed_legs:
        return None
    expiration = min(item[0] for item in parsed_legs)
    leg_parts = [f"{sign}{option_type}{strike:g}" for _, option_type, strike, sign in parsed_legs]
    return f"{expiration} {' / '.join(leg_parts)}"


def _days_until_date(date_value: str | None) -> int | None:
    if not date_value:
        return None
    try:
        target_date = datetime.fromisoformat(date_value).date()
    except ValueError:
        return None
    return (target_date - now_new_york().date()).days


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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
    return _parse_jsonl_lines(lines[-limit:])


def _read_all_jsonl(path: str | Path) -> list[dict[str, Any]]:
    audit_path = Path(path)
    if not audit_path.exists():
        return []
    return _parse_jsonl_lines(audit_path.read_text(encoding="utf-8").splitlines())


def _parse_jsonl_lines(lines: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in lines:
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
    return _filter_paper_logs_for_current_state(_read_recent_jsonl(path, 1000), limit=limit)


def _read_paper_performance_logs(path: str | Path) -> list[dict[str, Any]]:
    return _filter_paper_logs_for_current_state(_read_all_jsonl(path), limit=None)


def _filter_paper_logs_for_current_state(
    records: list[dict[str, Any]],
    *,
    limit: int | None,
) -> list[dict[str, Any]]:
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
    return relevant if limit is None else relevant[-limit:]


def _paper_performance_payload(
    records: list[dict[str, Any]],
    state,
    *,
    range_key: str = "all",
) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    created_at = state.created_at
    if created_at:
        points.append(
            {
                "time": created_at,
                "time_new_york": _ny_time_string(created_at),
                "equity": state.starting_equity,
                "total_pnl": 0.0,
                "open_positions": 0,
            }
        )

    for record in records:
        if record.get("event_type") != "paper_cycle":
            continue
        result = _nested_dict(record, "result")
        summary = _nested_dict(result or {}, "summary")
        if not summary:
            continue
        points.append(
            {
                "time": record.get("logged_at"),
                "time_new_york": record.get("logged_at_new_york") or _ny_time_string(record.get("logged_at")),
                "equity": summary.get("equity"),
                "total_pnl": summary.get("total_pnl"),
                "open_positions": summary.get("open_positions"),
            }
        )

    points = [point for point in points if point.get("equity") is not None]
    if not points:
        points = [
            {
                "time": state.updated_at,
                "time_new_york": _ny_time_string(state.updated_at),
                "equity": state.equity,
                "total_pnl": state.equity - state.starting_equity,
                "open_positions": len(state.open_positions),
            }
        ]

    headline_value = points[-1]["equity"]
    starting_value = points[0]["equity"]
    headline_change = None
    headline_change_pct = None
    if isinstance(headline_value, (int, float)) and isinstance(starting_value, (int, float)):
        headline_change = round(headline_value - starting_value, 2)
        if starting_value:
            headline_change_pct = round((headline_change / starting_value) * 100, 2)
    filtered_points = _filter_performance_points(points, range_key)
    if filtered_points:
        period_starting_value = filtered_points[0]["equity"]
        period_ending_value = filtered_points[-1]["equity"]
        if isinstance(period_starting_value, (int, float)) and isinstance(period_ending_value, (int, float)):
            headline_value = period_ending_value
            headline_change = round(period_ending_value - period_starting_value, 2)
            if period_starting_value:
                headline_change_pct = round((headline_change / period_starting_value) * 100, 2)
    return {
        "headline_value": headline_value,
        "headline_change": headline_change,
        "headline_change_pct": headline_change_pct,
        "points": filtered_points,
        "range_label": _range_label(range_key, "Paper account history"),
    }


def _live_performance_payload(snapshot, *, range_key: str = "all") -> dict[str, Any]:
    history = _read_live_equity_history(DEFAULT_LIVE_EQUITY_HISTORY_PATH, limit=5000)
    points = _filter_performance_points(history, range_key)
    if not points and snapshot.balances and snapshot.balances.get("net_liquidating_value") is not None:
        points = [
            {
                "time": snapshot.fetched_at,
                "time_new_york": _ny_time_string(snapshot.fetched_at),
                "equity": snapshot.balances.get("net_liquidating_value"),
                "cash": snapshot.balances.get("cash_balance"),
                "buying_power": snapshot.balances.get("derivative_buying_power"),
            }
        ]
    headline_value = points[-1]["equity"] if points else None
    headline_change = None
    headline_change_pct = None
    if points:
        starting_value = points[0]["equity"]
        if isinstance(headline_value, (int, float)) and isinstance(starting_value, (int, float)):
            headline_change = round(headline_value - starting_value, 2)
            if starting_value:
                headline_change_pct = round((headline_change / starting_value) * 100, 2)
    return {
        "headline_value": headline_value,
        "headline_change": headline_change,
        "headline_change_pct": headline_change_pct,
        "points": points,
        "range_label": _range_label(range_key, "Actual account history"),
    }


def _paper_analytics_payload(
    records: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    spec_reasons: Counter[str] = Counter()
    risk_reasons: Counter[str] = Counter()
    liquidity_blocks: Counter[str] = Counter()
    strategy_outcomes: Counter[str] = Counter()

    for record in records:
        event_type = str(record.get("event_type") or "")
        if event_type == "paper_candidate_spec_rejected":
            spec_reasons.update(str(reason) for reason in record.get("spec_reason_codes") or [])
        elif event_type == "paper_candidate_rejected":
            decision = record.get("risk_decision", {})
            if isinstance(decision, dict):
                risk_reasons.update(str(reason) for reason in decision.get("reason_codes") or [])
        elif event_type == "paper_scan_diagnostics":
            diagnostics = record.get("diagnostics", {})
            if isinstance(diagnostics, dict):
                blocks = diagnostics.get("liquidity_blocks", {})
                if isinstance(blocks, dict):
                    for reason, count in blocks.items():
                        liquidity_blocks[str(reason)] += int(count or 0)
        if event_type in {
            "paper_position_opened",
            "paper_position_closed",
            "paper_candidate_spec_rejected",
            "paper_candidate_rejected",
        }:
            candidate = record.get("candidate") or _nested_dict(record, "paper_closed_trade", "position") or {}
            if isinstance(candidate, dict):
                strategy_name = str(candidate.get("strategy_name") or "unknown")
                strategy_outcomes[f"{event_type}:{strategy_name}"] += 1

    return {
        "open_positions": summary.get("open_positions"),
        "closed_trades": summary.get("closed_trades"),
        "total_open_max_loss": summary.get("total_open_max_loss"),
        "top_spec_rejections": [[reason, count] for reason, count in spec_reasons.most_common(6)],
        "top_risk_rejections": [[reason, count] for reason, count in risk_reasons.most_common(6)],
        "top_liquidity_blocks": [[reason, count] for reason, count in liquidity_blocks.most_common(6)],
        "strategy_outcomes": [[reason, count] for reason, count in strategy_outcomes.most_common(12)],
    }


def _paper_ledger_payload(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ledger: list[dict[str, Any]] = []
    for record in records:
        event_type = str(record.get("event_type") or "")
        logged_at = record.get("logged_at")
        time_new_york = record.get("logged_at_new_york") or _ny_time_string(logged_at)
        if event_type == "paper_position_opened":
            candidate = record.get("candidate", {})
            risk = record.get("risk_decision", {})
            ledger.append(
                {
                    "time": logged_at,
                    "time_new_york": time_new_york,
                    "event_type": event_type,
                    "headline": "Opened position",
                    "symbol": record.get("symbol") or candidate.get("underlying"),
                    "strategy": candidate.get("strategy_name"),
                    "amount": candidate.get("expected_credit_or_debit"),
                    "max_loss": candidate.get("max_loss"),
                    "pnl": None,
                    "reasons": risk.get("reason_codes") if isinstance(risk, dict) else [],
                    "raw": record,
                }
            )
        elif event_type == "paper_position_closed":
            closed_trade = record.get("paper_closed_trade", {})
            position = closed_trade.get("position", {}) if isinstance(closed_trade, dict) else {}
            ledger.append(
                {
                    "time": logged_at,
                    "time_new_york": time_new_york,
                    "event_type": event_type,
                    "headline": "Closed position",
                    "symbol": record.get("symbol") or position.get("underlying"),
                    "strategy": position.get("strategy_name"),
                    "amount": position.get("expected_credit_or_debit"),
                    "max_loss": position.get("max_loss"),
                    "pnl": closed_trade.get("realized_pnl") if isinstance(closed_trade, dict) else None,
                    "reasons": [closed_trade.get("exit_reason")] if isinstance(closed_trade, dict) and closed_trade.get("exit_reason") else [],
                    "raw": record,
                }
            )
        elif event_type == "paper_candidate_spec_rejected":
            candidate = record.get("candidate", {})
            ledger.append(
                {
                    "time": logged_at,
                    "time_new_york": time_new_york,
                    "event_type": event_type,
                    "headline": "Spec rejected",
                    "symbol": record.get("symbol") or candidate.get("underlying"),
                    "strategy": candidate.get("strategy_name"),
                    "amount": candidate.get("expected_credit_or_debit"),
                    "max_loss": candidate.get("max_loss"),
                    "pnl": None,
                    "reasons": record.get("spec_reason_codes") or [],
                    "raw": record,
                }
            )
        elif event_type == "paper_candidate_rejected":
            candidate = record.get("candidate", {})
            decision = record.get("risk_decision", {})
            ledger.append(
                {
                    "time": logged_at,
                    "time_new_york": time_new_york,
                    "event_type": event_type,
                    "headline": "Risk rejected",
                    "symbol": record.get("symbol") or candidate.get("underlying"),
                    "strategy": candidate.get("strategy_name"),
                    "amount": candidate.get("expected_credit_or_debit"),
                    "max_loss": candidate.get("max_loss"),
                    "pnl": None,
                    "reasons": decision.get("reason_codes") if isinstance(decision, dict) else [],
                    "raw": record,
                }
            )
    return ledger[-200:]


def _live_ledger_payload() -> list[dict[str, Any]]:
    records = _read_recent_jsonl(DEFAULT_BOT_TRADE_JOURNAL_PATH, 200)
    ledger: list[dict[str, Any]] = []
    for record in records:
        candidate = _first_dict(
            record.get("candidate"),
            record.get("paper_position"),
            record.get("position"),
            _nested_dict(record, "trade_record", "candidate"),
            _nested_dict(record, "order", "candidate"),
        )
        ledger.append(
            {
                "time": record.get("logged_at") or record.get("opened_at"),
                "time_new_york": _ny_time_string(record.get("logged_at") or record.get("opened_at")),
                "event_type": record.get("event_type") or "journal",
                "headline": str(record.get("event_type") or "journal").replace("_", " ").title(),
                "symbol": candidate.get("underlying") or candidate.get("symbol") or record.get("symbol"),
                "strategy": candidate.get("strategy_name") or record.get("strategy_name"),
                "amount": candidate.get("expected_credit_or_debit") or record.get("expected_credit_or_debit"),
                "max_loss": candidate.get("max_loss") or record.get("max_loss"),
                "pnl": record.get("realized_pnl"),
                "reasons": record.get("reason_codes") or [],
                "raw": record,
            }
        )
    return ledger[-200:]


def _filter_performance_points(
    points: list[dict[str, Any]],
    range_key: str,
) -> list[dict[str, Any]]:
    normalized = _normalize_range_key(range_key)
    if normalized == "all":
        return points
    now = now_new_york().astimezone(UTC)
    windows = {
        "1d": timedelta(days=1),
        "1w": timedelta(days=7),
        "1m": timedelta(days=30),
    }
    threshold = now - windows[normalized]
    filtered = [
        point
        for point in points
        if (timestamp := _parse_timestamp(point.get("time"))) is not None
        and timestamp.astimezone(UTC) >= threshold
    ]
    return filtered or points[-1:]


def _filter_ledger_entries(
    entries: list[dict[str, Any]],
    ledger_filter: str,
) -> list[dict[str, Any]]:
    normalized = _normalize_ledger_filter(ledger_filter)
    if normalized == "all":
        return entries
    event_types = {
        "opened": {"paper_position_opened", "candidate_dry_run", "trade_logged"},
        "closed": {"paper_position_closed", "position_closed", "trade_closed"},
        "spec_rejected": {"paper_candidate_spec_rejected"},
        "risk_rejected": {"paper_candidate_rejected"},
    }[normalized]
    return [entry for entry in entries if str(entry.get("event_type") or "") in event_types]


def _normalize_range_key(range_key: str) -> str:
    normalized = str(range_key or "all").strip().lower()
    return normalized if normalized in {"1d", "1w", "1m", "all"} else "all"


def _normalize_ledger_filter(ledger_filter: str) -> str:
    normalized = str(ledger_filter or "all").strip().lower()
    allowed = {"all", "opened", "closed", "spec_rejected", "risk_rejected"}
    return normalized if normalized in allowed else "all"


def _range_label(range_key: str, default_label: str) -> str:
    labels = {
        "1d": "1D",
        "1w": "1W",
        "1m": "1M",
        "all": "All",
    }
    normalized = _normalize_range_key(range_key)
    return f"{default_label} · {labels[normalized]}"


def _append_live_equity_snapshot(snapshot) -> None:
    balances = snapshot.balances or {}
    equity = balances.get("net_liquidating_value")
    fetched_at = snapshot.fetched_at
    if equity is None or not fetched_at:
        return
    path = Path(DEFAULT_LIVE_EQUITY_HISTORY_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    last_records = _read_recent_jsonl(path, 1)
    if last_records:
        last = last_records[-1]
        if last.get("logged_at") == fetched_at and last.get("equity") == equity:
            return
    payload = {
        "logged_at": fetched_at,
        "logged_at_new_york": _ny_time_string(fetched_at),
        "equity": equity,
        "cash": balances.get("cash_balance"),
        "buying_power": balances.get("derivative_buying_power"),
        "account_type": "live",
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True))
        handle.write("\n")


def _read_live_equity_history(path: str | Path, limit: int) -> list[dict[str, Any]]:
    records = _read_recent_jsonl(path, limit)
    points: list[dict[str, Any]] = []
    for record in records:
        equity = record.get("equity")
        logged_at = record.get("logged_at")
        if equity is None or not logged_at:
            continue
        points.append(
            {
                "time": logged_at,
                "time_new_york": record.get("logged_at_new_york") or _ny_time_string(logged_at),
                "equity": equity,
                "cash": record.get("cash"),
                "buying_power": record.get("buying_power"),
            }
        )
    return points


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
    return max(0, int((now_new_york().astimezone(UTC) - timestamp.astimezone(UTC)).total_seconds()))


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
    return parse_timestamp(value, naive_timezone=UTC)


def _ny_time_string(value: str | None) -> str | None:
    return format_new_york_timestamp(value)


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
      color-scheme: light;
      --bg: #f5f8f3;
      --bg-2: #eef3ec;
      --panel: rgba(255,255,255,0.94);
      --panel-2: #fbfdf9;
      --panel-3: #f0f5ef;
      --border: rgba(18, 28, 20, 0.08);
      --border-strong: rgba(18, 28, 20, 0.14);
      --text: #0c160d;
      --muted: #667564;
      --good: #00c805;
      --warn: #c28a00;
      --bad: #d94b45;
      --accent: #00c805;
      --accent-soft: rgba(0,200,5,0.09);
      --blue: #0b6bcb;
      --shadow: 0 18px 42px rgba(20, 37, 22, 0.08);
      --radius: 22px;
    }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; }
    body {
      margin: 0;
      font-family: "Aptos", "Segoe UI Variable", "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
      background:
        radial-gradient(circle at top left, rgba(0,200,5,0.08), transparent 25%),
        radial-gradient(circle at top right, rgba(0,0,0,0.03), transparent 20%),
        linear-gradient(180deg, #fbfdf9 0%, #f4f8f2 100%);
      color: var(--text);
      font-size: 14px;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 28px;
      border-bottom: 1px solid var(--border);
      background: rgba(251, 253, 249, 0.88);
      backdrop-filter: blur(22px);
    }
    h1 { margin: 0; font-size: 18px; font-weight: 760; letter-spacing: -0.02em; }
    h2 {
      margin: 0 0 12px;
      font-size: 12px;
      font-weight: 750;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }
    main {
      display: grid;
      gap: 20px;
      padding: 26px;
      max-width: 1500px;
      margin: 0 auto;
    }
    section {
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(255,255,255,0.96), rgba(249,252,247,0.94));
      border-radius: var(--radius);
      padding: 20px;
      box-shadow: var(--shadow);
    }
    .toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .toolbar select, input {
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.92);
      color: var(--text);
      border-radius: 14px;
      padding: 11px 13px;
      font: inherit;
    }
    button {
      border: 1px solid rgba(0,0,0,0.04);
      background: linear-gradient(180deg, #131d15 0%, #0b120c 100%);
      color: white;
      border-radius: 999px;
      padding: 10px 16px;
      font-weight: 760;
      cursor: pointer;
      transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
      box-shadow: 0 10px 20px rgba(12, 22, 13, 0.08);
    }
    button.secondary {
      background: rgba(255,255,255,0.88);
      border-color: var(--border-strong);
      color: var(--text);
      box-shadow: none;
    }
    button.secondary.active {
      background: var(--accent-soft);
      border-color: rgba(0,200,5,0.32);
      color: #0d4e13;
    }
    button:hover { transform: translateY(-1px); border-color: rgba(0,0,0,0.14); }
    button:disabled { opacity: 0.55; cursor: progress; transform: none; }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      border-radius: 999px;
      border: 1px solid var(--border);
      padding: 0 12px;
      color: var(--muted);
      background: rgba(255,255,255,0.7);
      white-space: nowrap;
      font-weight: 650;
    }
    .good { color: var(--good); }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    .muted { color: var(--muted); }
    .hero {
      display: grid;
      grid-template-columns: minmax(0, 2.15fr) minmax(320px, 1fr);
      gap: 18px;
    }
    .hero-card {
      padding: 28px;
      border-radius: 28px;
      background:
        radial-gradient(circle at top left, rgba(0,200,5,0.10), transparent 26%),
        linear-gradient(180deg, rgba(255,255,255,0.98), rgba(246,250,244,0.98));
      border: 1px solid var(--border);
      min-height: 420px;
      display: grid;
      gap: 18px;
    }
    .headline {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }
    .eyebrow {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      margin-bottom: 8px;
      font-weight: 700;
    }
    .balance {
      font-size: 62px;
      font-weight: 780;
      line-height: 1;
      letter-spacing: -0.04em;
      margin: 0;
    }
    .balance-meta {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      align-items: center;
      margin-top: 10px;
      color: var(--muted);
    }
    .delta {
      font-weight: 700;
      color: var(--accent);
    }
    .hero-strip {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }
    .hero-chip {
      padding: 12px 14px;
      border-radius: 16px;
      border: 1px solid var(--border);
      background: rgba(245, 249, 243, 0.92);
    }
    .hero-chip .label {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .hero-chip .value {
      margin-top: 8px;
      font-size: 22px;
      font-weight: 760;
    }
    .chart-shell {
      position: relative;
      min-height: 260px;
      border-radius: 22px;
      border: 1px solid var(--border);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.96), rgba(246,250,244,0.92)),
        rgba(255,255,255,0.7);
      overflow: hidden;
      padding: 14px 16px 36px;
    }
    .chart-tooltip {
      position: absolute;
      min-width: 150px;
      pointer-events: none;
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid rgba(0, 0, 0, 0.08);
      background: rgba(11, 18, 12, 0.94);
      color: white;
      box-shadow: 0 16px 28px rgba(0, 0, 0, 0.18);
      z-index: 2;
    }
    .chart-tooltip strong {
      display: block;
      font-size: 16px;
      letter-spacing: -0.02em;
    }
    .chart-tooltip span {
      display: block;
      margin-top: 4px;
      font-size: 11px;
      color: rgba(255,255,255,0.72);
    }
    .chart-grid {
      position: absolute;
      inset: 0;
      opacity: 0.5;
      background:
        linear-gradient(180deg, transparent 24%, rgba(13,27,17,0.06) 25%, transparent 26%, transparent 49%, rgba(13,27,17,0.06) 50%, transparent 51%, transparent 74%, rgba(13,27,17,0.06) 75%, transparent 76%),
        linear-gradient(90deg, transparent 19%, rgba(13,27,17,0.04) 20%, transparent 21%, transparent 39%, rgba(13,27,17,0.04) 40%, transparent 41%, transparent 59%, rgba(13,27,17,0.04) 60%, transparent 61%, transparent 79%, rgba(13,27,17,0.04) 80%, transparent 81%);
      pointer-events: none;
    }
    .chart-meta {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      margin-top: 10px;
      color: var(--muted);
      font-size: 12px;
    }
    .chart-fallback {
      min-height: 220px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
    }
    .chart-fallback[hidden] {
      display: none;
    }
    .account-summary {
      display: grid;
      gap: 12px;
    }
    .summary-card {
      padding: 18px;
      border-radius: 20px;
      background: rgba(255,255,255,0.82);
      border: 1px solid var(--border);
    }
    .summary-value {
      font-size: 30px;
      font-weight: 740;
      margin-top: 8px;
      letter-spacing: -0.03em;
    }
    .summary-note {
      margin-top: 10px;
      color: var(--muted);
      line-height: 1.45;
    }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
    }
    .metric {
      min-height: 100px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.84);
      border-radius: 20px;
      padding: 16px;
    }
    .label { color: var(--muted); font-size: 12px; }
    .value { margin-top: 10px; font-size: 24px; font-weight: 760; overflow-wrap: anywhere; letter-spacing: -0.03em; }
    .analytics-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }
    .assistant-shell {
      display: grid;
      gap: 16px;
    }
    .assistant-layout {
      display: grid;
      grid-template-columns: minmax(0, 1.45fr) minmax(320px, 0.85fr);
      gap: 16px;
      align-items: start;
    }
    .assistant-thread-panel {
      border: 1px solid var(--border);
      border-radius: 24px;
      background: linear-gradient(180deg, rgba(255,255,255,0.97), rgba(247,250,245,0.94));
      padding: 18px;
      display: grid;
      gap: 14px;
      box-shadow: var(--shadow);
    }
    .assistant-thread {
      min-height: 320px;
      max-height: 520px;
      overflow: auto;
      display: grid;
      gap: 12px;
      padding-right: 4px;
    }
    .assistant-message {
      display: grid;
      gap: 5px;
      padding: 14px 16px;
      border-radius: 18px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.92);
    }
    .assistant-message.user {
      background: linear-gradient(180deg, rgba(0,200,5,0.08), rgba(0,200,5,0.04));
      border-color: rgba(0,200,5,0.16);
    }
    .assistant-message.assistant {
      background: #0d1610;
      color: #eff7ef;
    }
    .assistant-message.assistant .label,
    .assistant-message.assistant .muted {
      color: rgba(239,247,239,0.68);
    }
    .assistant-role {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      font-weight: 750;
      color: var(--muted);
    }
    .assistant-message.assistant .assistant-role {
      color: rgba(239,247,239,0.66);
    }
    .assistant-content {
      white-space: pre-wrap;
      line-height: 1.5;
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    .assistant-compose {
      display: grid;
      gap: 10px;
    }
    .assistant-compose textarea {
      width: 100%;
      min-height: 120px;
      resize: vertical;
      border-radius: 18px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.92);
      color: var(--text);
      padding: 14px 16px;
      font: inherit;
      line-height: 1.5;
    }
    .assistant-compose-row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    .assistant-compose-row select {
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.92);
      color: var(--text);
      border-radius: 14px;
      padding: 10px 12px;
      font: inherit;
    }
    .assistant-side {
      display: grid;
      gap: 14px;
    }
    .assistant-change {
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.9);
      border-radius: 18px;
      padding: 14px;
      display: grid;
      gap: 8px;
    }
    .assistant-change-title {
      font-weight: 760;
      letter-spacing: -0.02em;
    }
    .assistant-change-meta {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      display: grid;
      gap: 4px;
    }
    .assistant-code-block {
      margin: 0;
      padding: 14px 16px;
      border-radius: 16px;
      border: 1px solid var(--border);
      background: rgba(13,22,16,0.96);
      color: #eaf4ea;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font: inherit;
      line-height: 1.5;
      min-height: 120px;
      max-height: 280px;
      overflow: auto;
    }
    .assistant-empty {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
      border: 1px dashed var(--border);
      border-radius: 16px;
      padding: 14px;
      background: rgba(255,255,255,0.66);
    }
    .assistant-question-list {
      display: grid;
      gap: 8px;
    }
    .assistant-question {
      border: 1px solid rgba(0,200,5,0.12);
      background: rgba(0,200,5,0.06);
      color: var(--text);
      padding: 10px 12px;
      border-radius: 14px;
      font-size: 12px;
      line-height: 1.4;
    }
    .chip-grid {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .chip {
      border-radius: 999px;
      padding: 8px 12px;
      background: rgba(255,255,255,0.9);
      border: 1px solid var(--border);
      color: var(--text);
      font-size: 12px;
      display: inline-flex;
      gap: 8px;
      align-items: center;
    }
    .chip-count {
      color: var(--muted);
      font-weight: 700;
    }
    .activity-shell {
      display: grid;
      gap: 16px;
    }
    .activity-tabs {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
    }
    .activity-tab-buttons {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .activity-panel {
      display: none;
      gap: 14px;
    }
    .activity-panel.active {
      display: grid;
    }
    .split-panel {
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(300px, 0.9fr);
      gap: 16px;
      align-items: start;
    }
    .positions-card-grid {
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .position-card {
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(255,255,255,0.95), rgba(247,250,245,0.92));
      border-radius: 24px;
      padding: 18px;
      display: grid;
      gap: 14px;
    }
    .position-card-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
    }
    .position-symbol {
      font-size: 26px;
      font-weight: 780;
      letter-spacing: -0.03em;
    }
    .position-strategy {
      color: var(--muted);
      margin-top: 4px;
    }
    .position-structure {
      font-family: "Consolas", "SFMono-Regular", monospace;
      font-size: 12px;
      color: #294329;
      padding: 10px 12px;
      border-radius: 14px;
      background: rgba(0,200,5,0.06);
      border: 1px solid rgba(0,200,5,0.10);
    }
    .position-price-row {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }
    .position-stat {
      padding: 12px 13px;
      border-radius: 16px;
      background: var(--panel-2);
      border: 1px solid var(--border);
    }
    .position-stat .label {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .position-stat .value {
      font-size: 18px;
      margin-top: 8px;
    }
    .position-meta-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .position-meta {
      padding: 12px 13px;
      border-radius: 16px;
      background: rgba(255,255,255,0.88);
      border: 1px solid var(--border);
    }
    .position-exit {
      padding: 14px;
      border-radius: 18px;
      background: #0d1610;
      color: #eff7ef;
      display: grid;
      gap: 8px;
    }
    .position-exit .label {
      color: rgba(239,247,239,0.66);
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }
    .position-exit-lines {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .position-empty {
      padding: 24px;
      border: 1px dashed var(--border-strong);
      border-radius: 20px;
      color: var(--muted);
      background: rgba(255,255,255,0.6);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      text-align: left;
      border-bottom: 1px solid rgba(12, 22, 13, 0.06);
      padding: 12px 10px;
      vertical-align: top;
      overflow-wrap: anywhere;
      font-size: 12px;
    }
    th {
      color: var(--muted);
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      font-size: 11px;
    }
    tr:hover td { background: rgba(0,200,5,0.035); }
    .ledger-headline {
      font-weight: 700;
      color: var(--text);
    }
    .ledger-sub {
      color: var(--muted);
      margin-top: 3px;
    }
    .stack { display: grid; gap: 12px; }
    pre {
      margin: 0;
      overflow: auto;
      max-height: 520px;
      background: #f7faf6;
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 14px;
      color: #243124;
      line-height: 1.45;
      font-size: 12px;
    }
    details summary {
      cursor: pointer;
      color: var(--muted);
      font-weight: 700;
      margin-bottom: 12px;
    }
    .diagnostic-form {
      display: grid;
      grid-template-columns: repeat(5, minmax(120px, 1fr));
      gap: 10px;
      align-items: end;
    }
    .diagnostic-form label { display: grid; gap: 5px; color: var(--muted); font-size: 12px; }
    .diagnostic-form input, .diagnostic-form select { width: 100%; }
    .section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }
    .table-shell {
      max-height: 420px;
      overflow: auto;
      border: 1px solid var(--border);
      border-radius: 20px;
      background: rgba(255,255,255,0.86);
    }
    #logs-section, #details-section, #raw-logs-section {
      background: linear-gradient(180deg, rgba(255,255,255,0.94), rgba(248,251,246,0.94));
    }
    @media (max-width: 1160px) {
      .hero,
      .split-panel,
      .analytics-grid,
      .assistant-layout {
        grid-template-columns: 1fr;
      }
      .metric-grid {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
      .hero-strip {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .positions-card-grid {
        grid-template-columns: 1fr;
      }
    }
    @media (max-width: 820px) {
      header { align-items: flex-start; flex-direction: column; }
      main { padding: 16px; }
      .metric-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .position-price-row,
      .position-meta-grid,
      .position-exit-lines {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .assistant-compose-row {
        justify-content: stretch;
      }
      .diagnostic-form {
        grid-template-columns: 1fr 1fr;
      }
      .balance {
        font-size: 48px;
      }
    }
    @media (max-width: 560px) {
      .metric-grid {
        grid-template-columns: 1fr;
      }
      .hero-strip {
        grid-template-columns: 1fr;
      }
      .position-price-row,
      .position-meta-grid,
      .position-exit-lines {
        grid-template-columns: 1fr;
      }
      .diagnostic-form {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Trading Bot Control</h1>
      <div class="muted">Research-first options dashboard. All times shown in America/New_York.</div>
    </div>
    <div class="toolbar">
      <span id="mode-pill" class="pill">dry_run</span>
      <select id="account-select" title="Account view">
        <option value="paper">Paper Account</option>
        <option value="live">Actual Account</option>
      </select>
      <button id="refresh">Refresh</button>
      <button id="logout" class="secondary" hidden>Sign Out</button>
    </div>
  </header>

  <main id="login-section" hidden>
    <section style="max-width: 430px; margin: 54px auto;">
      <h2>Sign In</h2>
      <div class="muted" style="margin-bottom: 14px;">Use the remote UI credentials configured on the server.</div>
      <form id="login-form" class="stack">
        <label>Username
          <input id="login-username" name="username" autocomplete="username">
        </label>
        <label>Password
          <input id="login-password" name="password" type="password" autocomplete="current-password">
        </label>
        <button id="login-button" type="submit">Sign In</button>
      </form>
      <div id="login-error" class="bad" style="margin-top: 12px;"></div>
    </section>
  </main>

  <main id="app-shell" hidden>
    <section class="hero">
      <div class="hero-card">
        <div class="headline">
          <div>
            <div class="eyebrow" id="account-title">Paper Account</div>
            <div id="hero-balance" class="balance">$0.00</div>
            <div class="balance-meta">
              <span id="hero-change" class="delta">$0.00</span>
              <span id="hero-change-pct" class="muted">0.00%</span>
              <span id="hero-message" class="muted"></span>
            </div>
          </div>
          <div class="toolbar">
            <div class="toolbar">
              <button class="secondary range-button" data-range="1d" type="button">1D</button>
              <button class="secondary range-button" data-range="1w" type="button">1W</button>
              <button class="secondary range-button" data-range="1m" type="button">1M</button>
              <button class="secondary range-button" data-range="all" type="button">All</button>
            </div>
            <span id="connection-pill" class="pill">not loaded</span>
            <span class="pill" id="hero-range">history</span>
          </div>
        </div>
        <div class="chart-shell">
          <div class="chart-grid"></div>
          <svg id="equity-chart" viewBox="0 0 900 260" width="100%" height="260" preserveAspectRatio="none"></svg>
          <div id="chart-fallback" class="chart-fallback" hidden>No performance points yet.</div>
          <div id="chart-tooltip" class="chart-tooltip" hidden></div>
        </div>
        <div class="chart-meta">
          <span id="chart-start">Start: N/A</span>
          <span id="chart-end">Latest: N/A</span>
          <span id="chart-points">0 points</span>
        </div>
        <div class="hero-strip">
          <div class="hero-chip">
            <div class="label">Available Cash</div>
            <div id="hero-cash" class="value">$0.00</div>
          </div>
          <div class="hero-chip">
            <div class="label">Open Risk</div>
            <div id="hero-open-risk" class="value">$0.00</div>
          </div>
          <div class="hero-chip">
            <div class="label">Open Positions</div>
            <div id="hero-open-count" class="value">0</div>
          </div>
          <div class="hero-chip">
            <div class="label">Cycle Output</div>
            <div id="hero-cycle-output" class="value">0 / 0</div>
          </div>
        </div>
      </div>

      <div class="account-summary">
        <div class="summary-card">
          <div class="eyebrow">Runner</div>
          <div id="runtime-status" class="summary-value">N/A</div>
          <div class="summary-note">
            <div id="runtime-last-cycle">Last cycle: N/A</div>
            <div id="runtime-next-cycle">Next cycle: N/A</div>
            <div id="runtime-last-result">Result: N/A</div>
          </div>
        </div>
        <div class="summary-card">
          <div class="eyebrow">Connectivity</div>
          <div id="account-message" class="summary-value" style="font-size: 20px;">N/A</div>
          <div class="summary-note">
            <div id="view-note">Paper and actual account share one layout.</div>
            <div id="updated-at-note">Updated: N/A</div>
          </div>
        </div>
      </div>
    </section>

    <section>
      <div class="section-header">
        <h2>Overview</h2>
        <div class="muted">Primary balances first, secondary diagnostics below</div>
      </div>
      <div class="hero-strip" style="margin-bottom: 12px;">
        <div class="hero-chip"><div class="label">Equity / Net Liq</div><div id="metric-equity" class="value"></div></div>
        <div class="hero-chip"><div class="label">Cash</div><div id="metric-cash" class="value"></div></div>
        <div class="hero-chip"><div class="label">Buying Power</div><div id="metric-buying-power" class="value"></div></div>
        <div class="hero-chip"><div class="label">Open Max Loss</div><div id="metric-reserved-risk" class="value"></div></div>
      </div>
      <div class="metric-grid">
        <div class="metric"><div class="label">Realized P/L</div><div id="metric-realized" class="value"></div></div>
        <div class="metric"><div class="label">Unrealized P/L</div><div id="metric-unrealized" class="value"></div></div>
        <div class="metric"><div class="label">Total P/L</div><div id="metric-total-pnl" class="value"></div></div>
        <div class="metric"><div class="label">Return</div><div id="metric-return" class="value"></div></div>
        <div class="metric"><div class="label">Open Positions</div><div id="metric-open-positions" class="value"></div></div>
        <div class="metric"><div class="label">Closed Trades</div><div id="metric-closed-trades" class="value"></div></div>
        <div class="metric"><div class="label">Generated This Cycle</div><div id="metric-generated" class="value"></div></div>
        <div class="metric"><div class="label">Rejected This Cycle</div><div id="metric-rejected" class="value"></div></div>
      </div>
    </section>

    <section class="analytics-grid">
      <div>
        <div class="section-header"><h2>Spec Rejections</h2></div>
        <div id="spec-chip-grid" class="chip-grid"></div>
      </div>
      <div>
        <div class="section-header"><h2>Risk Rejections</h2></div>
        <div id="risk-chip-grid" class="chip-grid"></div>
      </div>
      <div>
        <div class="section-header"><h2>Liquidity Blocks</h2></div>
        <div id="liquidity-chip-grid" class="chip-grid"></div>
      </div>
    </section>

    <section class="assistant-shell">
      <div class="section-header">
        <h2>Research Assistant</h2>
        <div class="toolbar">
          <span class="pill" id="assistant-model-pill">model: N/A</span>
          <span class="pill" id="assistant-response-pill">research-only</span>
        </div>
      </div>
      <div class="assistant-layout">
        <div class="assistant-thread-panel">
          <div class="section-header">
            <div>
              <h2>Conversation</h2>
              <div class="muted">Ask about strategy state, open risk, or research changes. Strategy edits are returned as Codex-ready tasks, not auto-applied.</div>
            </div>
            <div class="toolbar">
              <button class="secondary" type="button" id="assistant-clear">Clear</button>
            </div>
          </div>
          <div id="assistant-thread" class="assistant-thread"></div>
          <div class="assistant-compose">
            <textarea id="assistant-input" placeholder="Ask a strategy question, ask for a report summary, or request a Codex-ready change proposal."></textarea>
            <div class="assistant-compose-row">
              <select id="assistant-mode">
                <option value="strategy">Strategy</option>
                <option value="review">Review</option>
                <option value="report">Report</option>
              </select>
              <button class="secondary" type="button" id="assistant-suggest-state">Ask about current state</button>
              <button class="secondary" type="button" id="assistant-suggest-improve">Suggest an improvement</button>
              <button class="primary" type="button" id="assistant-send">Send</button>
            </div>
          </div>
        </div>
        <div class="assistant-side">
          <div class="assistant-change">
            <div class="assistant-change-title">Codex Task</div>
            <div class="assistant-change-meta">Copy this into Codex to draft or modify strategy code after human review.</div>
            <pre id="assistant-codex-task" class="assistant-code-block">No task yet.</pre>
            <div class="toolbar">
              <button class="secondary" type="button" id="assistant-copy-task">Copy Task</button>
            </div>
          </div>
          <div class="assistant-change">
            <div class="assistant-change-title">Proposed Changes</div>
            <div id="assistant-proposed-changes" class="stack"></div>
          </div>
          <div class="assistant-change">
            <div class="assistant-change-title">Assistant Summary</div>
            <div id="assistant-summary" class="assistant-change-meta">No assistant response yet.</div>
            <div class="assistant-change-meta" style="margin-top: 8px;">
              <div>Needs human approval: <span id="assistant-needs-approval">N/A</span></div>
              <div>Confidence: <span id="assistant-confidence">N/A</span></div>
              <div>Follow-up questions: <span id="assistant-follow-ups">N/A</span></div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="activity-shell">
      <div class="activity-tabs">
        <div>
          <h2 style="margin-bottom: 6px;">Activity</h2>
          <div class="muted">Switch between holdings, ledger flow, and realized exits</div>
        </div>
      <div class="activity-tab-buttons">
        <button class="secondary activity-tab active" data-activity="positions" type="button">Positions</button>
        <button class="secondary activity-tab" data-activity="ledger" type="button">Ledger</button>
        <button class="secondary activity-tab" data-activity="closed" type="button">Closed Trades</button>
        <button class="secondary activity-tab" data-activity="research-queue" type="button">Research Queue</button>
      </div>
    </div>

      <div id="activity-positions" class="activity-panel active">
        <div class="split-panel">
          <div>
            <div class="section-header">
              <h2>Open Positions</h2>
              <div class="muted">Card-style holdings with entry, risk, and exit context</div>
            </div>
            <div id="positions-cards" class="positions-card-grid"></div>
          </div>
          <div class="stack">
            <section style="padding: 0; background: transparent; border: 0; box-shadow: none;">
              <div class="section-header">
                <h2>Strategy Outcomes</h2>
                <div class="muted">Recent open/close/rejection distribution</div>
              </div>
              <div id="strategy-chip-grid" class="chip-grid"></div>
            </section>
            <section style="padding: 0; background: transparent; border: 0; box-shadow: none;">
              <div class="section-header">
                <h2>Position Notes</h2>
                <div class="muted">Cards are sorted by the selected account view and current open holdings.</div>
              </div>
              <div class="position-empty">Use this view like a brokerage holdings screen: structure, mark, P/L, and exit context are grouped together per position.</div>
            </section>
          </div>
        </div>
      </div>

      <div id="activity-ledger" class="activity-panel">
        <div class="section-header">
          <h2>Trade Ledger</h2>
          <div class="toolbar">
            <div class="muted">Every open, close, spec rejection, and risk rejection</div>
            <select id="ledger-filter" title="Ledger filter">
              <option value="all">All events</option>
              <option value="opened">Opened</option>
              <option value="closed">Closed</option>
              <option value="spec_rejected">Spec rejected</option>
              <option value="risk_rejected">Risk rejected</option>
            </select>
          </div>
        </div>
        <div class="table-shell">
          <table>
            <thead>
              <tr>
                <th>Time</th><th>Event</th><th>Symbol</th><th>Strategy</th><th>Amount</th><th>P/L</th><th>Reasons</th>
              </tr>
            </thead>
            <tbody id="ledger-body"></tbody>
          </table>
        </div>
      </div>

      <div id="activity-closed" class="activity-panel">
        <div class="section-header">
          <h2>Closed Trades</h2>
          <div class="muted">Most recent realized exits</div>
        </div>
        <div class="table-shell">
          <table>
            <thead><tr><th>Symbol</th><th>Strategy</th><th>Closed</th><th>Reason</th><th>Realized P/L</th></tr></thead>
            <tbody id="closed-body"></tbody>
          </table>
      </div>
    </div>

      <div id="activity-research-queue" class="activity-panel">
        <div class="section-header">
          <h2>Research Queue</h2>
          <div class="toolbar">
            <div class="muted">Assistant-generated Codex tasks awaiting human review</div>
            <button class="secondary" type="button" id="refresh-research-queue">Refresh Queue</button>
          </div>
        </div>
        <div id="research-queue-summary" class="chip-grid"></div>
        <div id="research-queue-list" class="stack"></div>
      </div>
    </section>

    <section id="logs-section">
      <div class="section-header">
        <h2>Recent Logs</h2>
        <div class="toolbar">
          <button class="secondary" id="show-details">Detailed JSON</button>
          <button class="secondary" id="show-raw-logs">Raw Logs</button>
        </div>
      </div>
      <div class="table-shell">
        <table>
          <thead><tr><th>Time</th><th>Event</th><th>Summary</th></tr></thead>
          <tbody id="logs-body"></tbody>
        </table>
      </div>
    </section>

    <section id="details-section">
      <div class="section-header">
        <h2>Detailed Info</h2>
        <div class="muted">Full structured payload for the selected account view</div>
      </div>
      <pre id="details-output">{}</pre>
    </section>

    <section id="raw-logs-section">
      <div class="section-header">
        <h2>Raw Logs</h2>
        <div class="muted">Most recent audit payloads for the selected view</div>
      </div>
      <pre id="logs-output">[]</pre>
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
    const sessionUrl = "/api/session";
    const loginUrl = "/api/login";
    const logoutUrl = "/api/logout";
    const accountViewUrl = "/api/account-view";
    const logsUrl = "/api/logs";
    const runUrl = "/api/run-once";
    const assistantUrl = "/api/assistant";
    const researchQueueUrl = "/api/research-queue";
    const assistantStorageKey = "strategy-assistant-thread-v1";
    let currentAccount = "paper";
    let currentRange = "all";
    let currentLedgerFilter = "all";
    let currentActivity = "positions";
    let assistantMessages = [];
    let assistantBusy = false;
    let researchQueueItems = [];

    function text(id, value) {
      document.getElementById(id).textContent = value ?? "";
    }

    function money(value) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return "N/A";
      return "$" + numeric.toLocaleString(undefined, {maximumFractionDigits: 2});
    }

    function signedMoney(value) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return "N/A";
      const prefix = numeric > 0 ? "+" : "";
      return prefix + money(numeric);
    }

    function pct(value) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return "N/A";
      return numeric.toLocaleString(undefined, {maximumFractionDigits: 2}) + "%";
    }

    function signedPct(value) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return "N/A";
      const prefix = numeric > 0 ? "+" : "";
      return prefix + pct(numeric);
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

    function chipMarkup(items, emptyLabel) {
      if (!items || items.length === 0) {
        return `<span class="chip">${safe(emptyLabel)} <span class="chip-count">0</span></span>`;
      }
      return items.map(item => `
        <span class="chip">
          <span>${safe(item[0])}</span>
          <span class="chip-count">${safe(item[1])}</span>
        </span>
      `).join("");
    }

    function loadAssistantMessages() {
      try {
        const raw = localStorage.getItem(assistantStorageKey);
        if (!raw) {
          assistantMessages = [];
          return;
        }
        const parsed = JSON.parse(raw);
        assistantMessages = Array.isArray(parsed)
          ? parsed
              .filter(item => item && typeof item === "object")
              .map(item => ({
                role: String(item.role || "assistant"),
                content: String(item.content || "")
              }))
              .filter(item => item.content.trim())
              .slice(-24)
          : [];
      } catch {
        assistantMessages = [];
      }
    }

    function saveAssistantMessages() {
      try {
        localStorage.setItem(assistantStorageKey, JSON.stringify(assistantMessages.slice(-24)));
      } catch {
        return;
      }
    }

    function renderAssistantThread() {
      const thread = document.getElementById("assistant-thread");
      if (!assistantMessages.length) {
        thread.innerHTML = `
          <div class="assistant-empty">
            Ask about the current strategy state, explain a trade, or request a Codex-ready change proposal.
            The assistant will stay research-only and will not modify code automatically.
          </div>
        `;
        return;
      }
      thread.innerHTML = assistantMessages.map(message => `
        <article class="assistant-message ${safe(message.role === "user" ? "user" : "assistant")}">
          <div class="assistant-role">${safe(message.role === "user" ? "You" : "Assistant")}</div>
          <div class="assistant-content">${safe(message.content)}</div>
        </article>
      `).join("");
      thread.scrollTop = thread.scrollHeight;
    }

    function renderAssistantResponse(response) {
      text("assistant-summary", response.summary || "No summary provided.");
      text("assistant-needs-approval", response.needs_human_approval ? "Yes" : "No");
      text("assistant-confidence", Number.isFinite(Number(response.confidence)) ? `${Number(response.confidence).toFixed(2)}` : "N/A");
      text(
        "assistant-follow-ups",
        (response.follow_up_questions || []).length
          ? (response.follow_up_questions || []).join(" | ")
          : "None"
      );
      document.getElementById("assistant-codex-task").textContent = response.codex_task || "No task yet.";
      const changes = (response.proposed_changes || []).map(change => `
        <article class="assistant-change">
          <div class="assistant-change-title">${safe(change.title)}</div>
          <div class="assistant-change-meta">
            <div>${safe(change.rationale || "No rationale.")}</div>
            <div><strong>Files:</strong> ${safe((change.files || []).join(", ") || "N/A")}</div>
            <div><strong>Validation:</strong> ${safe((change.validation || []).join(", ") || "N/A")}</div>
            <div><strong>Risk:</strong> ${safe(change.risk_impact || "N/A")}</div>
          </div>
        </article>
      `);
      document.getElementById("assistant-proposed-changes").innerHTML = changes.join("")
        || '<div class="assistant-empty">No proposed code changes were returned.</div>';
      document.getElementById("assistant-response-pill").textContent = response.research_only ? "research-only" : "non-research";
    }

    function resetAssistantSidePanel() {
      text("assistant-summary", "No assistant response yet.");
      text("assistant-needs-approval", "N/A");
      text("assistant-confidence", "N/A");
      text("assistant-follow-ups", "N/A");
      document.getElementById("assistant-codex-task").textContent = "No task yet.";
      document.getElementById("assistant-proposed-changes").innerHTML = '<div class="assistant-empty">No proposed code changes yet.</div>';
      document.getElementById("assistant-response-pill").textContent = "research-only";
    }

    function appendAssistantMessage(role, content) {
      assistantMessages.push({
        role: role === "user" ? "user" : "assistant",
        content: String(content || "")
      });
      assistantMessages = assistantMessages.slice(-24);
      saveAssistantMessages();
      renderAssistantThread();
    }

    async function submitAssistantMessage(promptText = null) {
      if (assistantBusy) {
        return;
      }
      const input = document.getElementById("assistant-input");
      const message = (promptText ?? input.value ?? "").trim();
      if (!message) {
        return;
      }
      const mode = document.getElementById("assistant-mode").value || "strategy";
      assistantBusy = true;
      document.getElementById("assistant-send").disabled = true;
      appendAssistantMessage("user", message);
      input.value = "";
      try {
        const payload = {
          messages: assistantMessages,
          mode,
          account_type: currentAccount,
          range: currentRange,
          ledger_filter: currentLedgerFilter
        };
        const response = await apiFetch(assistantUrl, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok || result.error) {
          throw new Error(result.message || result.error || `Assistant request failed (${response.status})`);
        }
        if (result && result.assistant_reply) {
          appendAssistantMessage("assistant", result.assistant_reply);
        } else {
          appendAssistantMessage("assistant", "No assistant reply was returned.");
        }
        renderAssistantResponse(result || {});
        await loadResearchQueue();
      } catch (error) {
        text("assistant-summary", `Assistant request failed: ${error.message || error}`);
        text("assistant-needs-approval", "N/A");
        text("assistant-confidence", "N/A");
        text("assistant-follow-ups", "N/A");
        document.getElementById("assistant-response-pill").textContent = "error";
        appendAssistantMessage("assistant", `Assistant request failed: ${error.message || error}`);
      } finally {
        assistantBusy = false;
        document.getElementById("assistant-send").disabled = false;
      }
    }

    async function copyAssistantCodexTask() {
      const textValue = document.getElementById("assistant-codex-task").textContent || "";
      if (!textValue.trim()) {
        return;
      }
      try {
        await navigator.clipboard.writeText(textValue);
        document.getElementById("assistant-copy-task").textContent = "Copied";
        setTimeout(() => {
          document.getElementById("assistant-copy-task").textContent = "Copy Task";
        }, 1200);
      } catch {
        const selection = window.getSelection();
        const range = document.createRange();
        const node = document.getElementById("assistant-codex-task");
        range.selectNodeContents(node);
        selection.removeAllRanges();
        selection.addRange(range);
        document.execCommand("copy");
        selection.removeAllRanges();
      }
    }

    function setActivityPanel(activity) {
      currentActivity = activity || "positions";
      document.querySelectorAll(".activity-tab").forEach(button => {
        button.classList.toggle("active", button.dataset.activity === currentActivity);
      });
      document.querySelectorAll(".activity-panel").forEach(panel => {
        panel.classList.toggle("active", panel.id === `activity-${currentActivity}`);
      });
    }

    function showLogin(message = "") {
      document.getElementById("login-section").hidden = false;
      document.getElementById("app-shell").hidden = true;
      document.getElementById("logout").hidden = true;
      text("login-error", message);
    }

    function showApp() {
      document.getElementById("login-section").hidden = true;
      document.getElementById("app-shell").hidden = false;
      document.getElementById("logout").hidden = false;
      text("login-error", "");
    }

    async function apiFetch(url, options) {
      const response = await fetch(url, options);
      if (response.status === 401) {
        showLogin("Sign in required.");
        throw new Error("unauthorized");
      }
      return response;
    }

    function renderChart(performance) {
      const svg = document.getElementById("equity-chart");
      const fallback = document.getElementById("chart-fallback");
      const tooltip = document.getElementById("chart-tooltip");
      const points = (performance && performance.points) || [];
      const numericPoints = points.filter(point => Number.isFinite(Number(point.equity)));
      if (numericPoints.length === 0) {
        svg.innerHTML = "";
        fallback.hidden = false;
        tooltip.hidden = true;
        svg.onmousemove = null;
        svg.onmouseleave = null;
        text("chart-start", "Start: N/A");
        text("chart-end", "Latest: N/A");
        text("chart-points", "0 points");
        return;
      }
      fallback.hidden = true;
      const width = 900;
      const height = 260;
      const paddingX = 18;
      const paddingY = 18;
      const values = numericPoints.map(point => Number(point.equity));
      const minValue = Math.min(...values);
      const maxValue = Math.max(...values);
      const valueRange = maxValue - minValue;
      const range = Math.max(1, valueRange);
      const timestamps = numericPoints.map(point => Date.parse(point.time || ""));
      const validTimestamps = timestamps.filter(value => Number.isFinite(value));
      const minTime = validTimestamps.length ? Math.min(...validTimestamps) : null;
      const maxTime = validTimestamps.length ? Math.max(...validTimestamps) : null;
      const timeRange = minTime === null || maxTime === null ? 0 : maxTime - minTime;
      const xStep = numericPoints.length === 1 ? 0 : (width - paddingX * 2) / (numericPoints.length - 1);
      const coords = numericPoints.map((point, index) => {
        const time = timestamps[index];
        const timeRatio = Number.isFinite(time) && timeRange > 0
          ? (time - minTime) / timeRange
          : (numericPoints.length === 1 ? 0 : index / (numericPoints.length - 1));
        const x = paddingX + Math.max(0, Math.min(1, timeRatio)) * (width - paddingX * 2);
        const normalized = valueRange === 0 ? 0.5 : (Number(point.equity) - minValue) / range;
        const y = height - paddingY - normalized * (height - paddingY * 2);
        return { x, y, point };
      });
      const linePath = coords.map((coord, index) => `${index === 0 ? "M" : "L"} ${coord.x.toFixed(2)} ${coord.y.toFixed(2)}`).join(" ");
      const areaPath = `${linePath} L ${coords[coords.length - 1].x.toFixed(2)} ${(height - paddingY).toFixed(2)} L ${coords[0].x.toFixed(2)} ${(height - paddingY).toFixed(2)} Z`;
      const latest = coords[coords.length - 1];
      svg.innerHTML = `
        <defs>
          <linearGradient id="equity-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="rgba(0,200,5,0.22)"></stop>
            <stop offset="100%" stop-color="rgba(0,200,5,0.02)"></stop>
          </linearGradient>
        </defs>
        <path d="${areaPath}" fill="url(#equity-fill)"></path>
        <path d="${linePath}" fill="none" stroke="#00c805" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"></path>
        <line id="chart-guide" x1="${latest.x.toFixed(2)}" y1="${paddingY}" x2="${latest.x.toFixed(2)}" y2="${(height - paddingY).toFixed(2)}" stroke="rgba(0,0,0,0.16)" stroke-dasharray="5 5"></line>
        <circle cx="${latest.x.toFixed(2)}" cy="${latest.y.toFixed(2)}" r="5.5" fill="#00c805"></circle>
      `;
      text("chart-start", `Start: ${money(values[0])}`);
      text("chart-end", `Latest: ${money(values[values.length - 1])}`);
      text("chart-points", `${numericPoints.length} points`);
      svg.onmousemove = event => {
        const rect = svg.getBoundingClientRect();
        const xRatio = rect.width <= 0 ? 0 : (event.clientX - rect.left) / rect.width;
        const targetX = paddingX + Math.max(0, Math.min(1, xRatio)) * (width - paddingX * 2);
        let nearest = coords[0];
        let nearestDistance = Math.abs(coords[0].x - targetX);
        for (const coord of coords) {
          const distance = Math.abs(coord.x - targetX);
          if (distance < nearestDistance) {
            nearest = coord;
            nearestDistance = distance;
          }
        }
        const guide = document.getElementById("chart-guide");
        if (guide) {
          guide.setAttribute("x1", nearest.x.toFixed(2));
          guide.setAttribute("x2", nearest.x.toFixed(2));
        }
        tooltip.hidden = false;
        tooltip.innerHTML = `<strong>${money(nearest.point.equity)}</strong><span>${safe(timeNy(nearest.point.time))}</span>`;
        const left = Math.min(rect.width - 170, Math.max(12, (nearest.x / width) * rect.width + 12));
        const top = Math.max(12, (nearest.y / height) * rect.height - 54);
        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${top}px`;
      };
      svg.onmouseleave = () => {
        tooltip.hidden = true;
      };
    }

    async function loadResearchQueue() {
      const payload = await apiFetch(`${researchQueueUrl}?limit=100`).then(r => r.json());
      researchQueueItems = payload.items || [];
      renderResearchQueue(payload);
    }

    function renderResearchQueue(payload = {}) {
      const counts = payload.status_counts || {};
      document.getElementById("research-queue-summary").innerHTML = chipMarkup(Object.entries(counts), "No queued research tasks");
      const items = researchQueueItems.map(item => {
        const changes = (item.proposed_changes || []).map(change => `
          <div class="assistant-question">
            <strong>${safe(change.title || "Proposed change")}</strong><br>
            ${safe(change.rationale || "")}<br>
            Files: ${safe((change.files || []).join(", ") || "N/A")}
          </div>
        `).join("");
        return `
          <article class="assistant-change">
            <div class="section-header" style="align-items: start;">
              <div>
                <div class="assistant-change-title">${safe(item.summary || "Research task")}</div>
                <div class="assistant-change-meta">
                  <div>${safe(item.created_at_new_york || timeNy(item.created_at))}</div>
                  <div>${safe(item.status || "pending_review")} | ${safe(item.assistant_model || "N/A")} | confidence ${safe(item.confidence ?? "N/A")}</div>
                </div>
              </div>
              <span class="pill">${safe(item.mode || "strategy")}</span>
            </div>
            <div class="assistant-change-meta">
              <div><strong>User ask:</strong> ${safe(item.user_message || "N/A")}</div>
              <div><strong>Approval:</strong> ${item.needs_human_approval ? "Required" : "Not required"}</div>
            </div>
            <pre class="assistant-code-block">${safe(item.codex_task || "No Codex task.")}</pre>
            ${changes ? `<div class="assistant-question-list">${changes}</div>` : ""}
          </article>
        `;
      });
      document.getElementById("research-queue-list").innerHTML = items.join("")
        || '<div class="assistant-empty">No assistant-generated Codex tasks are queued yet.</div>';
    }

    async function loadStatus() {
      const status = await apiFetch(statusUrl).then(r => r.json());
      text("mode-pill", status.mode);
      text("assistant-model-pill", `model: ${status.research_model_default || "N/A"}`);
    }

    async function loadAccountView() {
      currentAccount = document.getElementById("account-select").value;
      const view = await apiFetch(
        `${accountViewUrl}?type=${encodeURIComponent(currentAccount)}&range=${encodeURIComponent(currentRange)}&ledger_filter=${encodeURIComponent(currentLedgerFilter)}`
      ).then(r => r.json());
      showApp();
      renderAccountView(view);
      await loadLogs();
      await loadStatus();
    }

    function renderAccountView(view) {
      const metrics = view.metrics || {};
      const runtime = view.runtime || {};
      const analytics = view.analytics || {};
      const performance = view.performance || {};
      text("account-title", view.title || "Account");
      text("hero-balance", money(performance.headline_value ?? metrics.equity));
      text("hero-change", signedMoney(performance.headline_change ?? metrics.total_pnl));
      text("hero-change-pct", signedPct(performance.headline_change_pct ?? metrics.return_pct));
      text("hero-message", view.message || "");
      text("hero-range", performance.range_label || "history");
      text("hero-cash", money(metrics.cash));
      text("hero-open-risk", money(metrics.reserved_risk));
      text("hero-open-count", plain(metrics.open_positions));
      text(
        "hero-cycle-output",
        `${plain(runtime.generated_candidates ?? 0)} gen / ${plain(runtime.rejected_candidates ?? 0)} rej`
      );
      text("account-message", view.connected ? (view.message || "Connected") : (view.message || "Disconnected"));
      text("view-note", currentAccount === "paper" ? "Virtual paper account with full bot ledger and rolling risk budget." : "Actual account is read-only. Equity history is built from stored account snapshots.");
      text("updated-at-note", `Updated: ${timeNy(view.updated_at)}`);
      document.getElementById("ledger-filter").value = currentLedgerFilter;
      document.querySelectorAll(".range-button").forEach(button => {
        const isActive = button.dataset.range === currentRange;
        button.classList.toggle("active", isActive);
      });

      const pill = document.getElementById("connection-pill");
      pill.textContent = view.connected ? "connected" : "not connected";
      pill.className = "pill " + (view.connected ? "good" : "warn");

      text("runtime-status", runtime.status || (view.connected ? "connected" : "not connected"));
      text("runtime-last-cycle", `Last cycle: ${runtime.last_cycle_at_new_york || "N/A"}`);
      text("runtime-next-cycle", `Next cycle: ${runtime.next_cycle_estimate_new_york || "N/A"}`);
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
      text("metric-total-pnl", signedMoney(metrics.total_pnl));
      text("metric-return", signedPct(metrics.return_pct));
      text("metric-open-positions", plain(metrics.open_positions));
      text("metric-closed-trades", plain(metrics.closed_trades));
      text("metric-generated", plain(runtime.generated_candidates));
      text("metric-rejected", plain(runtime.rejected_candidates));

      document.getElementById("spec-chip-grid").innerHTML = chipMarkup(analytics.top_spec_rejections, "No spec rejections");
      document.getElementById("risk-chip-grid").innerHTML = chipMarkup(analytics.top_risk_rejections, "No risk rejections");
      document.getElementById("liquidity-chip-grid").innerHTML = chipMarkup(analytics.top_liquidity_blocks, "No liquidity blocks");
      document.getElementById("strategy-chip-grid").innerHTML = chipMarkup(analytics.strategy_outcomes, "No recent strategy events");

      renderChart(performance);

      const positionCards = (view.positions || []).map(position => {
        const riskScore = [
          position.max_loss === null || position.max_loss === undefined ? "" : `risk ${money(position.max_loss)}`,
          position.entry_score === null || position.entry_score === undefined ? "" : `score ${position.entry_score}`
        ].filter(Boolean).join(" / ");
        const exitMonitor = position.exit_monitor || {};
        const currentClose = exitMonitor.current_close_value === null || exitMonitor.current_close_value === undefined
          ? "N/A"
          : money(exitMonitor.current_close_value);
        const targetClose = exitMonitor.target_close_value === null || exitMonitor.target_close_value === undefined
          ? "N/A"
          : money(exitMonitor.target_close_value);
        const stopClose = exitMonitor.stop_close_value === null || exitMonitor.stop_close_value === undefined
          ? "N/A"
          : money(exitMonitor.stop_close_value);
        const timeExit = exitMonitor.days_until_time_exit === null || exitMonitor.days_until_time_exit === undefined
          ? "N/A"
          : `${exitMonitor.days_until_time_exit}d`;
        return `
          <article class="position-card">
            <div class="position-card-header">
              <div>
                <div class="position-symbol">${safe(position.symbol)}</div>
                <div class="position-strategy">${safe(position.strategy)} · ${safe(position.managed_by)}</div>
              </div>
              <span class="pill">${safe(position.direction)} · ${safe(position.quantity)}</span>
            </div>
            <div class="position-structure">${safe(position.structure_summary || "N/A")}</div>
            <div class="position-price-row">
              <div class="position-stat"><div class="label">Avg Price</div><div class="value">${money(position.average_open_price)}</div></div>
              <div class="position-stat"><div class="label">Mark</div><div class="value">${money(position.mark_value ?? position.mark_price)}</div></div>
              <div class="position-stat"><div class="label">P/L</div><div class="value">${signedMoney(position.day_pnl)}</div></div>
              <div class="position-stat"><div class="label">Risk / Score</div><div class="value">${safe(riskScore || "N/A")}</div></div>
            </div>
            <div class="position-meta-grid">
              <div class="position-meta"><div class="label">Opened</div><div class="value">${safe(exitMonitor.opened_at_new_york || timeNy(position.opened_at))}</div></div>
              <div class="position-meta"><div class="label">Managed By</div><div class="value">${safe(position.managed_by)}</div></div>
            </div>
            <div class="position-exit">
              <div class="label">Exit Monitor</div>
              <div class="position-exit-lines">
                <div><strong>${currentClose}</strong><br><span>Current close</span></div>
                <div><strong>${targetClose}</strong><br><span>Profit target</span></div>
                <div><strong>${stopClose}</strong><br><span>Stop threshold</span></div>
                <div><strong>${timeExit}</strong><br><span>${exitMonitor.time_exit_active ? "Time exit active" : "Until time exit"}</span></div>
              </div>
            </div>
          </article>
        `;
      });
      document.getElementById("positions-cards").innerHTML = positionCards.join("")
        || '<div class="position-empty">No positions for this account view.</div>';

      const closedRows = (view.closed_trades || []).map(trade => `
        <tr>
          <td>${safe(trade.symbol)}</td>
          <td>${safe(trade.strategy)}</td>
          <td>${safe(timeNy(trade.closed_at))}</td>
          <td>${safe(trade.exit_reason)}</td>
          <td>${signedMoney(trade.realized_pnl)}</td>
        </tr>
      `);
      document.getElementById("closed-body").innerHTML = closedRows.join("")
        || '<tr><td colspan="5">No closed trades in this account view.</td></tr>';

      const ledgerRows = (view.ledger || []).slice().reverse().map(entry => `
        <tr>
          <td>${safe(entry.time_new_york || timeNy(entry.time))}</td>
          <td>
            <div class="ledger-headline">${safe(entry.headline)}</div>
            <div class="ledger-sub">${safe(entry.event_type)}</div>
          </td>
          <td>${safe(entry.symbol)}</td>
          <td>${safe(entry.strategy)}</td>
          <td>${money(entry.amount)}</td>
          <td>${entry.pnl === null || entry.pnl === undefined ? "N/A" : signedMoney(entry.pnl)}</td>
          <td>${safe((entry.reasons || []).join(", ") || "N/A")}</td>
        </tr>
      `);
      document.getElementById("ledger-body").innerHTML = ledgerRows.join("")
        || '<tr><td colspan="7">No ledger entries for this view.</td></tr>';

      document.getElementById("details-output").textContent = JSON.stringify(view.details || {}, null, 2);
    }

    async function loadLogs() {
      const payload = await apiFetch(`${logsUrl}?type=${currentAccount === "paper" ? "paper" : "dry"}&limit=50`).then(r => r.json());
      const rows = (payload.records || []).map(record => {
        const result = record.result || {};
        const candidate = record.candidate || {};
        const summary = result.summary || {};
        const shortSummary = [
          candidate.strategy_name,
          result.generated_candidates === undefined ? "" : `generated ${result.generated_candidates}`,
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
      document.getElementById("logs-output").textContent = JSON.stringify(payload.records || [], null, 2);
    }

    document.getElementById("account-select").addEventListener("change", loadAccountView);
    document.querySelectorAll(".activity-tab").forEach(button => {
      button.addEventListener("click", () => setActivityPanel(button.dataset.activity));
    });
    document.getElementById("ledger-filter").addEventListener("change", event => {
      currentLedgerFilter = event.target.value || "all";
      loadAccountView();
    });
    document.querySelectorAll(".range-button").forEach(button => {
      button.addEventListener("click", () => {
        currentRange = button.dataset.range || "all";
        loadAccountView();
      });
    });
    document.getElementById("refresh").addEventListener("click", loadAccountView);
    document.getElementById("logout").addEventListener("click", async () => {
      await fetch(logoutUrl, {method: "POST"});
      showLogin("Signed out.");
    });
    document.getElementById("show-details").addEventListener("click", () => {
      document.getElementById("details-section").scrollIntoView({behavior: "smooth"});
    });
    document.getElementById("show-raw-logs").addEventListener("click", () => {
      document.getElementById("raw-logs-section").scrollIntoView({behavior: "smooth"});
    });
    document.getElementById("assistant-send").addEventListener("click", () => {
      submitAssistantMessage();
    });
    document.getElementById("assistant-clear").addEventListener("click", () => {
      assistantMessages = [];
      saveAssistantMessages();
      renderAssistantThread();
      resetAssistantSidePanel();
      document.getElementById("assistant-input").value = "";
    });
    document.getElementById("assistant-copy-task").addEventListener("click", copyAssistantCodexTask);
    document.getElementById("refresh-research-queue").addEventListener("click", loadResearchQueue);
    document.getElementById("assistant-suggest-state").addEventListener("click", () => {
      document.getElementById("assistant-input").value =
        "Summarize the current account state, open positions, total open risk, and the main strategy bottlenecks.";
      document.getElementById("assistant-input").focus();
    });
    document.getElementById("assistant-suggest-improve").addEventListener("click", () => {
      document.getElementById("assistant-input").value =
        "Propose one research-only strategy improvement based on the current account, and format it as a Codex-ready task.";
      document.getElementById("assistant-input").focus();
    });
    document.getElementById("assistant-input").addEventListener("keydown", event => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        submitAssistantMessage();
      }
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
        const result = await apiFetch(runUrl, {
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
    document.getElementById("login-form").addEventListener("submit", async event => {
      event.preventDefault();
      const button = document.getElementById("login-button");
      button.disabled = true;
      text("login-error", "");
      try {
        const payload = {
          username: document.getElementById("login-username").value,
          password: document.getElementById("login-password").value
        };
        const response = await fetch(loginUrl, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
        if (response.status === 401) {
          showLogin("Invalid username or password.");
          return;
        }
        showApp();
        await loadAccountView();
      } finally {
        button.disabled = false;
      }
    });

    async function initialize() {
      loadAssistantMessages();
      renderAssistantThread();
      resetAssistantSidePanel();
      const session = await fetch(sessionUrl).then(r => r.json());
      if (!session.authenticated) {
        showLogin();
        return;
      }
      setActivityPanel(currentActivity);
      showApp();
      await loadAccountView();
      await loadResearchQueue();
    }

    initialize();
  </script>
</body>
</html>
"""
