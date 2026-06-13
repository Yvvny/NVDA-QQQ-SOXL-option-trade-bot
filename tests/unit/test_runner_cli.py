import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path

from trading_bot.cli import main
from trading_bot.config.settings import load_settings
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
        settings=_lenient_credit_test_settings(),
        source="tastytrade",
        tastytrade_data_source=_FakeTastytradeDataSource(),
        audit_log_path=audit_log,
    )

    result = runner.run_once()

    assert result.source == "tastytrade"
    assert result.generated_candidates >= 1
    assert audit_log.exists()


def test_cli_exit_matrix_outputs_summary_and_variant_reports(tmp_path, capsys):
    scenario_file = tmp_path / "exit_matrix_scenarios.json"
    output_dir = tmp_path / "reports"
    scenario_file.write_text(_exit_matrix_fixture_json(), encoding="utf-8")

    exit_code = main(
        [
            "exit-matrix",
            "--scenario-file",
            str(scenario_file),
            "--output-dir",
            str(output_dir),
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert Path(output["summary_path"]).exists()
    assert (output_dir / "e1_backtest.json").exists()
    assert (output_dir / "e2_backtest.json").exists()
    assert (output_dir / "e3_backtest.json").exists()
    assert (output_dir / "e4_backtest.json").exists()
    assert output["report"]["scenario_count"] == 1


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
                _contract(expiration, "call", 510, 0.55, 0.79, 0.81),
                _contract(expiration, "call", 512, 0.30, 0.29, 0.31),
                _contract(expiration, "put", 450, -0.25, 0.49, 0.51),
                _contract(expiration, "put", 449, -0.10, 0.24, 0.26),
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


def _lenient_credit_test_settings():
    settings = load_settings(env={})
    return replace(
        settings,
        strategy=replace(
            settings.strategy,
            min_entry_score=50,
            credit_spread_min_planned_reward_risk=0.20,
        ),
        selection=replace(settings.selection, enabled=False),
    )


def _exit_matrix_fixture_json() -> str:
    return json.dumps(
        {
            "scenarios": [
                {
                    "trade_id": "fixture_1",
                    "entry_date": "2026-01-01",
                    "candidate": {
                        "strategy_name": "put_credit_spread",
                        "underlying": "QQQ",
                        "legs": [
                            {
                                "action": "sell",
                                "quantity": 1,
                                "contract": {
                                    "symbol": "QQQ 2026-06-19 450 put",
                                    "underlying": "QQQ",
                                    "expiration": "2026-06-19",
                                    "strike": 450,
                                    "option_type": "put",
                                    "bid": 0.45,
                                    "ask": 0.55,
                                    "mid": 0.50,
                                    "delta": -0.25,
                                    "volume": 100,
                                    "open_interest": 1000,
                                },
                            },
                            {
                                "action": "buy",
                                "quantity": 1,
                                "contract": {
                                    "symbol": "QQQ 2026-06-19 449 put",
                                    "underlying": "QQQ",
                                    "expiration": "2026-06-19",
                                    "strike": 449,
                                    "option_type": "put",
                                    "bid": 0.20,
                                    "ask": 0.30,
                                    "mid": 0.25,
                                    "delta": -0.10,
                                    "volume": 100,
                                    "open_interest": 1000,
                                },
                            },
                        ],
                        "dte": 30,
                        "entry_score": 80,
                        "max_profit": 50,
                        "max_loss": 50,
                        "expected_credit_or_debit": 50,
                        "reason_codes": ["fixture"],
                        "exit_plan": {
                            "profit_target_pct": 0.5,
                            "stop_loss_multiple": 2.5,
                            "time_exit_dte": 21,
                        },
                        "quantity": 1,
                    },
                    "exit_snapshots": [
                        {"date": "2026-01-05", "dte": 26, "mark_price": 0.25},
                        {"date": "2026-01-08", "dte": 23, "mark_price": 0.21},
                    ],
                }
            ]
        }
    )
