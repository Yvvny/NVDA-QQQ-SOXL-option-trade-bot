import base64
import json
import threading
import urllib.error
import urllib.request

from trading_bot.api import UiServerConfig, build_ui_server
from trading_bot.api.server import _live_position_view, _ny_time_string, _read_recent_paper_logs
from trading_bot.cli import build_parser


def test_cli_exposes_ui_command():
    parser = build_parser()
    args = parser.parse_args(["ui", "--host", "127.0.0.1", "--port", "9000"])

    assert args.command == "ui"
    assert args.host == "127.0.0.1"
    assert args.port == 9000


def test_ui_server_serves_status_and_runs_dry_run(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    server = build_ui_server(
        UiServerConfig(
            host="127.0.0.1",
            port=0,
            audit_log_path=str(audit_path),
        )
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"

    try:
        status = _get_json(f"{base_url}/api/status")
        account = _get_json(f"{base_url}/api/account")
        paper_view = _get_json(f"{base_url}/api/account-view?type=paper")
        run_result = _post_json(
            f"{base_url}/api/run-once",
            {"source": "mock", "symbol": "QQQ", "target_dte": 30, "max_candidates": 1},
        )
        audit = _get_json(f"{base_url}/api/audit?limit=5")
        html = urllib.request.urlopen(base_url, timeout=5).read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status["mode"] == "dry_run"
    assert account["source"] == "tastytrade"
    assert account["connected"] is False
    assert paper_view["account_type"] == "paper"
    assert paper_view["metrics"]["equity"] == 2000.0
    assert run_result["accepted"] == 1
    assert audit_path.exists()
    event_types = {record["event_type"] for record in audit["records"]}
    assert "scan_diagnostics" in event_types
    assert "candidate_dry_run" in event_types
    assert "Trading Bot Control" in html
    assert "account-select" in html


def test_ui_server_requires_basic_auth_when_configured(monkeypatch):
    monkeypatch.setenv("UI_AUTH_USERNAME", "admin")
    monkeypatch.setenv("UI_AUTH_PASSWORD", "secret")
    server = build_ui_server(UiServerConfig(host="127.0.0.1", port=0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"

    try:
        try:
            urllib.request.urlopen(f"{base_url}/api/health", timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:  # pragma: no cover - defensive branch for clearer test failure.
            raise AssertionError("Expected unauthenticated request to be rejected.")

        token = base64.b64encode(b"admin:secret").decode("ascii")
        request = urllib.request.Request(
            f"{base_url}/api/health",
            headers={"Authorization": f"Basic {token}"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload["ok"] is True


def test_live_position_view_labels_manual_and_bot_managed_positions():
    position = {
        "symbol": "QQQ   260619C00510000",
        "instrument_type": "Equity Option",
        "quantity": 1,
        "quantity_direction": "Long",
        "average_open_price": 3.2,
        "mark_price": 1.63,
        "mark": 163.0,
    }

    manual = _live_position_view(position, None)
    bot_managed = _live_position_view(
        position,
        {
            "strategy_name": "call_debit_spread",
            "entry_score": 82,
            "max_loss": 150,
            "exit_plan": {"profit_target_pct": 50},
        },
    )

    assert manual["managed_by"] == "Manual / External"
    assert manual["strategy"] == "Manual / External"
    assert manual["entry_score"] is None
    assert bot_managed["managed_by"] == "Bot"
    assert bot_managed["strategy"] == "call_debit_spread"
    assert bot_managed["entry_score"] == 82
    assert bot_managed["max_loss"] == 150


def test_paper_logs_only_include_current_default_state_records(tmp_path):
    audit_path = tmp_path / "paper_audit.jsonl"
    audit_path.write_text(
        "\n".join(
            [
                '{"event_type":"paper_position_opened","logged_at":"2026-05-18T20:40:53+00:00"}',
                '{"event_type":"paper_cycle","logged_at":"2026-05-18T20:40:54+00:00","result":{"state_path":"C:\\\\Temp\\\\paper.json"}}',
                '{"event_type":"paper_position_opened","logged_at":"2026-05-18T20:50:53+00:00"}',
                '{"event_type":"paper_cycle","logged_at":"2026-05-18T20:50:54+00:00","result":{"state_path":"docs\\\\reports\\\\paper_account.json"}}',
            ]
        ),
        encoding="utf-8",
    )

    records = _read_recent_paper_logs(audit_path, 10)

    assert [record["logged_at_new_york"] for record in records] == [
        "2026-05-18 16:50:53 EDT",
        "2026-05-18 16:50:54 EDT",
    ]


def test_ny_time_string_converts_utc_to_new_york():
    assert _ny_time_string("2026-05-18T22:12:38+00:00") == "2026-05-18 18:12:38 EDT"


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
