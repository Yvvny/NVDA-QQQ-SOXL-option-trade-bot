import json
from dataclasses import dataclass
from datetime import UTC, date, datetime

from trading_bot.cli import main
from trading_bot.core.enums import OptionType
from trading_bot.core.models import OptionContract, UnderlyingQuote
from trading_bot.data.tastytrade_source import TastytradeMarketSnapshot
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
    audit_path = tmp_path / "paper.jsonl"
    PaperTradingSimulator(
        source="mock",
        state_path=state_path,
        audit_log_path=audit_path,
    ).run_once()

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


def test_paper_mode_records_scan_diagnostics_for_no_candidate_cycle(tmp_path):
    state_path = tmp_path / "paper.json"
    audit_path = tmp_path / "paper.jsonl"
    simulator = PaperTradingSimulator(
        source="tastytrade",
        state_path=state_path,
        audit_log_path=audit_path,
        tastytrade_data_source=_IlliquidTastytradeDataSource(),
    )

    result = simulator.run_once()
    audit_text = audit_path.read_text(encoding="utf-8")
    diagnostics = [
        json.loads(line)
        for line in audit_text.splitlines()
        if '"event_type": "paper_scan_diagnostics"' in line
    ][0]["diagnostics"]

    assert result.generated_candidates == 0
    assert result.opened_positions == 0
    assert "paper_scan_diagnostics" in audit_text
    assert "all_contracts_failed_liquidity_filters" in audit_text
    assert "missing_delta" in audit_text
    assert "reason_codes" in diagnostics
    assert "contracts" in diagnostics
    assert "score_breakdown" not in audit_text
    assert "score_reason_codes" not in audit_text


@dataclass(frozen=True)
class _IlliquidTastytradeDataSource:
    def fetch_snapshot(self, symbol: str, target_dte: int):
        expiration = date(2026, 6, 19)
        return TastytradeMarketSnapshot(
            symbol=symbol,
            expiration=expiration,
            dte=30,
            underlying_quote=UnderlyingQuote(
                symbol=symbol,
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                bid=500.0,
                ask=500.2,
                last=500.1,
            ),
            option_contracts=(
                OptionContract(
                    symbol=f"{symbol} {expiration.isoformat()} 450 put",
                    underlying=symbol,
                    expiration=expiration,
                    strike=450,
                    option_type=OptionType.PUT,
                    bid=0.10,
                    ask=0.30,
                    mid=0.20,
                    delta=None,
                    volume=0,
                    open_interest=0,
                ),
            ),
        )
