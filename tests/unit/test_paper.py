from trading_bot.cli import main
from trading_bot.paper import PaperTradingSimulator


def test_paper_simulator_opens_virtual_position_and_persists_state(tmp_path):
    state_path = tmp_path / "paper.json"
    audit_path = tmp_path / "paper.jsonl"
    simulator = PaperTradingSimulator(
        source="mock",
        state_path=state_path,
        audit_log_path=audit_path,
        starting_equity=2000,
    )

    result = simulator.run_once()
    state = simulator.load_state()

    assert result.summary["starting_equity"] == 2000
    assert result.opened_positions == 1
    assert state_path.exists()
    assert audit_path.exists()
    assert len(state.open_positions) == 1
    assert state.to_summary()["open_positions"] == 1
    assert state.to_summary()["equity"] == 2000


def test_paper_status_cli_reads_virtual_state(tmp_path, capsys):
    state_path = tmp_path / "paper.json"
    PaperTradingSimulator(source="mock", state_path=state_path).run_once()

    exit_code = main(["paper-status", "--state-path", str(state_path)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"starting_equity": 2000.0' in output
    assert '"open_positions": 1' in output


def test_paper_once_cli_writes_virtual_state(tmp_path, capsys):
    state_path = tmp_path / "paper.json"
    audit_path = tmp_path / "paper.jsonl"

    exit_code = main(
        [
            "paper-once",
            "--source",
            "mock",
            "--state-path",
            str(state_path),
            "--audit-log",
            str(audit_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"opened_positions": 1' in output
    assert state_path.exists()


def test_strict_spec_paper_mode_records_spec_warnings(tmp_path):
    state_path = tmp_path / "paper.json"
    audit_path = tmp_path / "paper.jsonl"
    simulator = PaperTradingSimulator(
        source="mock",
        state_path=state_path,
        audit_log_path=audit_path,
        starting_equity=2000,
        strict_spec=True,
    )

    result = simulator.run_once()
    audit_text = audit_path.read_text(encoding="utf-8")

    assert result.strict_spec is True
    assert result.opened_positions == 1
    assert "paper_candidate_spec_warning" in audit_text
