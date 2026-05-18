from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from trading_bot.config.settings import BotSettings, load_settings
from trading_bot.runner import DryRunBotRunner


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
        if parsed.path == "/api/health":
            self._send_json({"ok": True, "mode": load_settings().risk.default_mode})
            return
        if parsed.path == "/api/status":
            self._send_json(_status_payload(self.server_config.audit_log_path))
            return
        if parsed.path == "/api/config":
            self._send_json(asdict(load_settings()))
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
    }


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
            records.append(payload)
    return records


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
    const auditUrl = "/api/audit?limit=12";
    const runUrl = "/api/run-once";

    function text(id, value) {
      document.getElementById(id).textContent = value ?? "";
    }

    function safetyRow(name, value) {
      const cls = value ? "bad" : "good";
      return `<div><span class="${cls}">${value ? "blocked" : "safe"}</span> ${name}</div>`;
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
  </script>
</body>
</html>
"""
