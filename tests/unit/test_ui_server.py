import base64
import json
import threading
import http.cookiejar
import urllib.error
import urllib.request
from types import SimpleNamespace

from trading_bot.api import UiServerConfig, build_ui_server
from trading_bot.api.server import (
    _live_position_view,
    _ny_time_string,
    _paper_position_view,
    _read_recent_paper_logs,
)
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
    assert status["server_time"].endswith("-04:00") or status["server_time"].endswith("-05:00")
    assert account["source"] == "tastytrade"
    assert isinstance(account["connected"], bool)
    assert paper_view["account_type"] == "paper"
    assert paper_view["metrics"]["equity"] == 2000.0
    assert "performance" in paper_view
    assert "ledger" in paper_view
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
        html = urllib.request.urlopen(base_url, timeout=5).read().decode("utf-8")
        assert "Sign In" in html

        try:
            urllib.request.urlopen(f"{base_url}/api/health", timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:  # pragma: no cover - defensive branch for clearer test failure.
            raise AssertionError("Expected unauthenticated request to be rejected.")

        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        login_request = urllib.request.Request(
            f"{base_url}/api/login",
            data=json.dumps({"username": "admin", "password": "secret"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(login_request, timeout=5) as response:
            login_payload = json.loads(response.read().decode("utf-8"))

        with opener.open(f"{base_url}/api/health", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

        logout_request = urllib.request.Request(f"{base_url}/api/logout", data=b"{}", method="POST")
        with opener.open(logout_request, timeout=5) as response:
            logout_payload = json.loads(response.read().decode("utf-8"))

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

    assert login_payload["ok"] is True
    assert payload["ok"] is True
    assert logout_payload["ok"] is True


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
            "max_profit": 100,
            "exit_plan": {"profit_target_pct": 0.5},
        },
    )

    assert manual["managed_by"] == "Manual / External"
    assert manual["strategy"] == "Manual / External"
    assert manual["entry_score"] is None
    assert bot_managed["managed_by"] == "Bot"
    assert bot_managed["strategy"] == "call_debit_spread"
    assert bot_managed["entry_score"] == 82
    assert bot_managed["max_loss"] == 150
    assert bot_managed["exit_monitor"]["target_close_value"] == 53.2


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


def test_paper_view_exposes_performance_and_ledger(tmp_path):
    from trading_bot.api.server import _paper_ledger_payload, _paper_performance_payload
    from trading_bot.paper import PaperAccountState

    logs = [
        {
            "event_type": "paper_position_opened",
            "logged_at": "2026-05-18T20:50:53+00:00",
            "logged_at_new_york": "2026-05-18 16:50:53 EDT",
            "symbol": "QQQ",
            "candidate": {
                "underlying": "QQQ",
                "strategy_name": "put_credit_spread",
                "expected_credit_or_debit": 25,
                "max_loss": 75,
            },
            "risk_decision": {"reason_codes": ["approved"]},
        },
        {
            "event_type": "paper_cycle",
            "logged_at": "2026-05-18T20:50:54+00:00",
            "logged_at_new_york": "2026-05-18 16:50:54 EDT",
            "result": {
                "summary": {
                    "equity": 2010.0,
                    "total_pnl": 10.0,
                    "open_positions": 1,
                },
            },
        },
        {
            "event_type": "paper_candidate_rejected",
            "logged_at": "2026-05-19T20:50:54+00:00",
            "logged_at_new_york": "2026-05-19 16:50:54 EDT",
            "candidate": {
                "underlying": "NVDA",
                "strategy_name": "call_debit_spread",
                "expected_credit_or_debit": 42,
                "max_loss": 142,
            },
            "risk_decision": {"reason_codes": ["spec_normal_trade_risk_above_20pct_equity"]},
        },
    ]
    performance = _paper_performance_payload(logs, PaperAccountState())
    ledger = _paper_ledger_payload(logs)

    assert any(point["equity"] == 2010.0 for point in performance["points"])
    assert any(entry["headline"] == "Opened position" for entry in ledger)
    assert any(entry["headline"] == "Risk rejected" for entry in ledger)


def test_paper_position_view_exposes_exit_monitor(monkeypatch):
    from trading_bot.api import server
    from trading_bot.paper import PaperLeg, PaperPosition

    monkeypatch.setattr(
        server,
        "now_new_york",
        lambda: server.parse_timestamp("2026-06-03T12:00:00-04:00"),
    )
    position = PaperPosition(
        position_id="p1",
        opened_at="2026-06-03T10:00:00-04:00",
        underlying="QQQ",
        strategy_name="put_credit_spread",
        dte_at_entry=29,
        entry_score=75.0,
        max_profit=15.5,
        max_loss=84.5,
        expected_credit_or_debit=15.5,
        price_effect="credit",
        entry_value=-15.5,
        legs=(
            PaperLeg(
                symbol="QQQ   260702P00700000",
                action="sell",
                quantity=1,
                option_type="put",
                strike=700.0,
                expiration="2026-07-02",
                entry_mid=5.9,
            ),
            PaperLeg(
                symbol="QQQ   260702P00699000",
                action="buy",
                quantity=1,
                option_type="put",
                strike=699.0,
                expiration="2026-07-02",
                entry_mid=5.745,
            ),
        ),
        exit_plan={
            "profit_target_pct": 0.5,
            "stop_loss_multiple": 2.5,
            "time_exit_dte": 21,
        },
        last_mark_value=-11.5,
        unrealized_pnl=4.0,
        last_marked_at="2026-06-03T12:10:00-04:00",
    )

    view = _paper_position_view(position)

    assert view["exit_monitor"]["current_close_value"] == 11.5
    assert view["exit_monitor"]["target_close_value"] == 7.75
    assert view["exit_monitor"]["stop_close_value"] == 54.25
    assert view["exit_monitor"]["days_until_time_exit"] == 8


def test_performance_range_and_ledger_filters(monkeypatch):
    from trading_bot.api import server

    monkeypatch.setattr(
        server,
        "now_new_york",
        lambda: server.parse_timestamp("2026-06-03T12:00:00-04:00"),
    )
    points = [
        {"time": "2026-05-01T12:00:00-04:00", "equity": 2000.0},
        {"time": "2026-05-30T12:00:00-04:00", "equity": 2010.0},
        {"time": "2026-06-03T08:00:00-04:00", "equity": 2020.0},
    ]
    filtered_points = server._filter_performance_points(points, "1d")
    assert [point["equity"] for point in filtered_points] == [2020.0]

    entries = [
        {"event_type": "paper_position_opened"},
        {"event_type": "paper_position_closed"},
        {"event_type": "paper_candidate_spec_rejected"},
        {"event_type": "paper_candidate_rejected"},
    ]
    filtered_ledger = server._filter_ledger_entries(entries, "spec_rejected")
    assert [entry["event_type"] for entry in filtered_ledger] == ["paper_candidate_spec_rejected"]


def test_live_equity_history_is_persisted_and_exposed(tmp_path, monkeypatch):
    from trading_bot.api import server

    history_path = tmp_path / "live_account_equity.jsonl"
    monkeypatch.setattr(server, "DEFAULT_LIVE_EQUITY_HISTORY_PATH", str(history_path))

    snapshot_one = SimpleNamespace(
        balances={
            "net_liquidating_value": 5000.0,
            "cash_balance": 2100.0,
            "derivative_buying_power": 3200.0,
        },
        fetched_at="2026-06-03T09:30:00-04:00",
    )
    snapshot_two = SimpleNamespace(
        balances={
            "net_liquidating_value": 5075.0,
            "cash_balance": 2175.0,
            "derivative_buying_power": 3275.0,
        },
        fetched_at="2026-06-03T11:30:00-04:00",
    )

    server._append_live_equity_snapshot(snapshot_one)
    server._append_live_equity_snapshot(snapshot_two)
    server._append_live_equity_snapshot(snapshot_two)

    history = server._read_live_equity_history(history_path, limit=10)
    performance = server._live_performance_payload(snapshot_two, range_key="all")

    assert len(history) == 2
    assert history[-1]["equity"] == 5075.0
    assert performance["points"][-1]["equity"] == 5075.0
    assert performance["range_label"].endswith("All")


def test_ny_time_string_converts_utc_to_new_york():
    assert _ny_time_string("2026-05-18T22:12:38+00:00") == "2026-05-18 18:12:38 EDT"


def test_ny_time_string_preserves_new_york_timestamp():
    assert _ny_time_string("2026-06-03T12:47:35-04:00") == "2026-06-03 12:47:35 EDT"


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
