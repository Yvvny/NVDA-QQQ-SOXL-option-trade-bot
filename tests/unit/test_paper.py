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
    assert state.to_summary()["available_cash"] == 1850


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


def test_paper_state_timestamps_are_new_york_offset(tmp_path):
    state_path = tmp_path / "paper.json"
    audit_path = tmp_path / "paper.jsonl"
    state = PaperTradingSimulator(
        source="mock",
        state_path=state_path,
        audit_log_path=audit_path,
    ).run_once().summary

    assert state["updated_at"].endswith("-04:00") or state["updated_at"].endswith("-05:00")


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


def test_paper_cli_allows_experimental_flag(tmp_path, capsys):
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
            "--strict-spec",
            "--paper-experimental",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"strict_spec": true' in output.lower()


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


def test_paper_records_position_paths_and_persists_exit_matrix_scenarios(tmp_path):
    state_path = tmp_path / "paper.json"
    audit_path = tmp_path / "paper.jsonl"
    path_log_path = tmp_path / "paper_position_paths.jsonl"
    scenario_path = tmp_path / "paper_exit_matrix_scenarios.json"
    simulator = PaperTradingSimulator(
        source="tastytrade",
        state_path=state_path,
        audit_log_path=audit_path,
        position_paths_path=path_log_path,
        exit_matrix_scenarios_path=scenario_path,
        tastytrade_data_source=_ClosableTastytradeDataSource(),
    )

    first = simulator.run_once()
    second = simulator.run_once(cycle_index=2)
    scenario_payload = json.loads(scenario_path.read_text(encoding="utf-8"))
    path_lines = path_log_path.read_text(encoding="utf-8").splitlines()

    assert first.opened_positions == 1
    assert second.closed_positions == 1
    assert scenario_path.exists()
    assert path_log_path.exists()
    assert len(scenario_payload["scenarios"]) == 1
    assert scenario_payload["scenarios"][0]["trade_id"]
    assert len(scenario_payload["scenarios"][0]["exit_snapshots"]) >= 1
    assert any("paper_position_path_snapshot" in line for line in path_lines)


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


class _ClosableTastytradeDataSource:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_snapshot(self, symbol: str, target_dte: int):
        self.calls += 1
        expiration = date(2026, 6, 19)
        if self.calls == 1:
            short_bid, short_ask = 0.49, 0.51
            long_bid, long_ask = 0.24, 0.26
            call_510 = (0.79, 0.81, 0.55)
            call_512 = (0.29, 0.31, 0.30)
        else:
            short_bid, short_ask = 0.20, 0.22
            long_bid, long_ask = 0.09, 0.11
            call_510 = (1.95, 1.97, None)
            call_512 = (0.31, 0.33, None)
        return TastytradeMarketSnapshot(
            symbol=symbol,
            expiration=expiration,
            dte=30,
            underlying_quote=UnderlyingQuote(
                symbol=symbol,
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                bid=510.0,
                ask=510.2,
                last=510.1,
            ),
            option_contracts=(
                OptionContract(
                    symbol=f"{symbol} {expiration.isoformat()} 450 put",
                    underlying=symbol,
                    expiration=expiration,
                    strike=450,
                    option_type=OptionType.PUT,
                    bid=short_bid,
                    ask=short_ask,
                    mid=(short_bid + short_ask) / 2,
                    delta=-0.25,
                    volume=100,
                    open_interest=1000,
                ),
                OptionContract(
                    symbol=f"{symbol} {expiration.isoformat()} 449 put",
                    underlying=symbol,
                    expiration=expiration,
                    strike=449,
                    option_type=OptionType.PUT,
                    bid=long_bid,
                    ask=long_ask,
                    mid=(long_bid + long_ask) / 2,
                    delta=-0.10,
                    volume=100,
                    open_interest=1000,
                ),
                OptionContract(
                    symbol=f"{symbol} {expiration.isoformat()} 510 call",
                    underlying=symbol,
                    expiration=expiration,
                    strike=510,
                    option_type=OptionType.CALL,
                    bid=call_510[0],
                    ask=call_510[1],
                    mid=(call_510[0] + call_510[1]) / 2,
                    delta=call_510[2],
                    volume=100,
                    open_interest=1000,
                ),
                OptionContract(
                    symbol=f"{symbol} {expiration.isoformat()} 512 call",
                    underlying=symbol,
                    expiration=expiration,
                    strike=512,
                    option_type=OptionType.CALL,
                    bid=call_512[0],
                    ask=call_512[1],
                    mid=(call_512[0] + call_512[1]) / 2,
                    delta=call_512[2],
                    volume=100,
                    open_interest=1000,
                ),
            ),
        )
