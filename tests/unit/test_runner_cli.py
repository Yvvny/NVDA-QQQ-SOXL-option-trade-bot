import json
from dataclasses import dataclass
from datetime import UTC, date, datetime

from trading_bot.cli import main
from trading_bot.core.enums import OptionType
from trading_bot.core.models import OptionContract, UnderlyingQuote
from trading_bot.data.tastytrade_source import TastytradeMarketSnapshot
from trading_bot.runner import DryRunBotRunner


def test_dry_run_runner_runs_one_mock_cycle_and_writes_audit_log(tmp_path):
    audit_log = tmp_path / "audit.jsonl"

    result = DryRunBotRunner(audit_log_path=audit_log).run_once()

    assert result.source == "mock"
    assert result.mode == "dry_run"
    assert result.generated_candidates >= 1
    assert result.attempted_candidates == 1
    assert result.accepted == 1
    assert audit_log.exists()


def test_dry_run_runner_repeated_cycles_can_be_bounded(tmp_path):
    audit_log = tmp_path / "audit.jsonl"

    results = DryRunBotRunner(audit_log_path=audit_log).run(cycles=2, interval_seconds=0)

    assert [result.cycle_index for result in results] == [1, 2]
    assert audit_log.read_text(encoding="utf-8").count("candidate_dry_run") == 2


def test_cli_run_once_outputs_json_and_writes_audit_log(tmp_path, capsys):
    audit_log = tmp_path / "audit.jsonl"

    exit_code = main(["run-once", "--audit-log", str(audit_log)])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["mode"] == "dry_run"
    assert output["accepted"] == 1
    assert audit_log.exists()


def test_runner_can_use_tastytrade_source_with_injected_snapshot(tmp_path):
    audit_log = tmp_path / "audit.jsonl"
    runner = DryRunBotRunner(
        source="tastytrade",
        tastytrade_data_source=_FakeTastytradeDataSource(),
        audit_log_path=audit_log,
    )

    result = runner.run_once()

    assert result.source == "tastytrade"
    assert result.generated_candidates >= 1
    assert audit_log.exists()


@dataclass(frozen=True)
class _FakeTastytradeDataSource:
    def fetch_snapshot(self, symbol: str, target_dte: int):
        expiration = date(2026, 6, 19)
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
                _contract(expiration, "call", 510, 0.55, 0.75, 0.85),
                _contract(expiration, "call", 512, 0.30, 0.25, 0.35),
                _contract(expiration, "put", 450, -0.25, 0.45, 0.55),
                _contract(expiration, "put", 449, -0.10, 0.20, 0.30),
            ),
        )


def _contract(
    expiration: date,
    option_type: str,
    strike: float,
    delta: float,
    bid: float,
    ask: float,
) -> OptionContract:
    return OptionContract(
        symbol=f"QQQ {expiration.isoformat()} {strike:g} {option_type}",
        underlying="QQQ",
        expiration=expiration,
        strike=strike,
        option_type=OptionType(option_type),
        bid=bid,
        ask=ask,
        mid=(bid + ask) / 2,
        delta=delta,
        volume=100,
        open_interest=1000,
    )
