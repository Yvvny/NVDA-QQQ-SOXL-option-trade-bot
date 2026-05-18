import json
import threading
import urllib.request

from trading_bot.api import UiServerConfig, build_ui_server
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
    assert run_result["accepted"] == 1
    assert audit_path.exists()
    assert len(audit["records"]) == 1
    assert "Trading Bot Control" in html


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
