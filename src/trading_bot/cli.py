from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date

from trading_bot.api import UiServerConfig, run_ui_server
from trading_bot.backtest import load_scenarios_from_json, run_exit_matrix
from trading_bot.broker import fetch_tastytrade_account_snapshot
from trading_bot.config.settings import load_settings
from trading_bot.core.time_utils import now_new_york
from trading_bot.data.qqq_chain_archive import DEFAULT_QQQ_SPOOL_ROOT, QqqFullChainCollector
from trading_bot.data.tastytrade_source import TastytradeSdkDataSource
from trading_bot.paper import DEFAULT_PAPER_STATE_PATH, PaperTradingSimulator
from trading_bot.research_bot import (
    ChatGPTResearchExportWriter,
    OpenAIResearchClient,
    ResearchReportWriter,
    ResearchReviewer,
    build_research_input_from_audit_log,
)
from trading_bot.runner import DryRunBotRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-bot",
        description="Research-first, dry-run-first options trading assistant.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("config", help="Print the effective bot configuration as JSON.")
    subparsers.add_parser("status", help="Print the current system mode and safety defaults.")
    account = subparsers.add_parser(
        "account",
        help="Print read-only tastytrade account status as JSON.",
    )
    account.add_argument(
        "--summary",
        action="store_true",
        help="Print a compact masked summary instead of full balance/position details.",
    )

    run_once = subparsers.add_parser("run-once", help="Run one safe dry-run scan cycle.")
    _add_run_arguments(run_once, include_loop_arguments=False)

    run = subparsers.add_parser("run", help="Run repeated safe dry-run scan cycles.")
    _add_run_arguments(run, include_loop_arguments=True)

    paper_status = subparsers.add_parser("paper-status", help="Print virtual paper account status.")
    paper_status.add_argument(
        "--state-path",
        default=str(DEFAULT_PAPER_STATE_PATH),
        help="Path to the virtual paper account JSON state.",
    )

    paper_once = subparsers.add_parser("paper-once", help="Run one virtual paper cycle.")
    _add_paper_arguments(paper_once, include_loop_arguments=False)

    paper_run = subparsers.add_parser(
        "paper-run",
        help="Run repeated virtual paper cycles. Use --days 30 for a one-month trial.",
    )
    _add_paper_arguments(paper_run, include_loop_arguments=True)

    research_review = subparsers.add_parser(
        "research-review",
        help="Generate a read-only JSON research report from paper audit logs.",
    )
    research_review.add_argument(
        "--audit-log",
        default="docs/reports/paper_audit.jsonl",
        help="Paper audit JSONL path to review.",
    )
    research_review.add_argument(
        "--date",
        default=None,
        help="UTC report date in YYYY-MM-DD format. Defaults to today's UTC date.",
    )
    research_review.add_argument(
        "--output-dir",
        default="docs/reports/research",
        help="Directory where the JSON report will be written.",
    )
    research_review.add_argument(
        "--model",
        default=None,
        help="OpenAI model to use. Defaults to OPENAI_RESEARCH_MODEL or gpt-5.5.",
    )
    research_review.add_argument(
        "--max-records",
        type=int,
        default=1000,
        help="Maximum recent audit records to read.",
    )

    research_export = subparsers.add_parser(
        "research-export",
        help="Export a ChatGPT Plus-ready Markdown research packet without API calls.",
    )
    research_export.add_argument(
        "--audit-log",
        default="docs/reports/paper_audit.jsonl",
        help="Paper audit JSONL path to export.",
    )
    research_export.add_argument(
        "--date",
        default=None,
        help="UTC report date in YYYY-MM-DD format. Defaults to today's UTC date.",
    )
    research_export.add_argument(
        "--output-dir",
        default="docs/reports/research",
        help="Directory where the Markdown export will be written.",
    )
    research_export.add_argument(
        "--max-records",
        type=int,
        default=1000,
        help="Maximum recent audit records to read.",
    )

    ui = subparsers.add_parser("ui", help="Start the local safe dry-run web UI.")
    ui.add_argument("--host", default="127.0.0.1", help="Host interface for the local UI.")
    ui.add_argument("--port", type=int, default=8765, help="Port for the local UI.")
    ui.add_argument(
        "--audit-log",
        default="docs/reports/trade_audit.jsonl",
        help="JSONL audit log path for UI runs.",
    )

    exit_matrix = subparsers.add_parser(
        "exit-matrix",
        help="Run the documented exit-parameter experiment matrix on backtest scenarios.",
    )
    exit_matrix.add_argument(
        "--scenario-file",
        required=True,
        help="JSON file containing a list of backtest scenarios or {\"scenarios\": [...]}",
    )
    exit_matrix.add_argument(
        "--output-dir",
        default="docs/reports/backtests/exit_matrix",
        help="Directory where the matrix summary and per-variant reports will be written.",
    )
    exit_matrix.add_argument(
        "--initial-equity",
        type=float,
        default=2000.0,
        help="Initial account equity for the backtest runs.",
    )

    collect_qqq_chain = subparsers.add_parser(
        "collect-qqq-chain",
        help="Collect one full QQQ option-chain snapshot into the cloud spool archive.",
    )
    collect_qqq_chain.add_argument(
        "--spool-root",
        default=str(DEFAULT_QQQ_SPOOL_ROOT),
        help="Root directory for raw chain snapshots, diagnostics, and manifests.",
    )
    collect_qqq_chain.add_argument(
        "--max-contracts-per-batch",
        type=int,
        default=500,
        help="Maximum option contracts to subscribe to per market-data batch.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()

    if args.command == "config":
        print(json.dumps(asdict(settings), indent=2, sort_keys=True))
        return 0

    if args.command in {None, "status"}:
        payload = {
            "mode": settings.risk.default_mode,
            "live_trading_default_allowed": settings.forbidden.allow_live_trading_default,
            "allow_0dte": settings.forbidden.allow_0dte,
            "allow_naked_options": settings.forbidden.allow_naked_options,
            "allow_market_orders_options": settings.forbidden.allow_market_orders_options,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "account":
        snapshot = fetch_tastytrade_account_snapshot()
        payload = _account_summary(snapshot) if args.summary else asdict(snapshot)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "run-once":
        runner = DryRunBotRunner(
            settings=settings,
            source=args.source,
            audit_log_path=args.audit_log,
            max_candidates_per_cycle=args.max_candidates,
            symbol=args.symbol,
            target_dte=args.target_dte,
            quote_timeout_seconds=args.quote_timeout_seconds,
            max_contracts=args.max_contracts,
        )
        result = runner.run_once()
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "ui":
        run_ui_server(
            UiServerConfig(
                host=args.host,
                port=args.port,
                audit_log_path=args.audit_log,
            )
        )
        return 0

    if args.command == "run":
        runner = DryRunBotRunner(
            settings=settings,
            source=args.source,
            audit_log_path=args.audit_log,
            max_candidates_per_cycle=args.max_candidates,
            symbol=args.symbol,
            target_dte=args.target_dte,
            quote_timeout_seconds=args.quote_timeout_seconds,
            max_contracts=args.max_contracts,
        )
        results = runner.run(cycles=args.cycles, interval_seconds=args.interval_seconds)
        print(json.dumps([result.to_dict() for result in results], indent=2, sort_keys=True))
        return 0

    if args.command == "paper-status":
        simulator = PaperTradingSimulator(state_path=args.state_path)
        print(json.dumps(simulator.load_state().to_summary(), indent=2, sort_keys=True))
        return 0

    if args.command == "paper-once":
        simulator = _paper_simulator_from_args(args, settings)
        result = simulator.run_once()
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    if args.command == "paper-run":
        simulator = _paper_simulator_from_args(args, settings)
        results = simulator.run(
            cycles=args.cycles,
            interval_seconds=args.interval_seconds,
            days=args.days,
        )
        print(json.dumps([result.to_dict() for result in results], indent=2, sort_keys=True))
        return 0

    if args.command == "research-review":
        report_date = date.fromisoformat(args.date) if args.date else None
        research_input = build_research_input_from_audit_log(
            args.audit_log,
            report_date=report_date,
            max_records=args.max_records,
        )
        client = OpenAIResearchClient.from_env(model=args.model)
        reviewer = ResearchReviewer(client, model=client.model)
        artifact = reviewer.review_to_artifact(research_input)
        output_path = ResearchReportWriter(args.output_dir).write(artifact)
        print(json.dumps({"report_path": str(output_path)}, indent=2, sort_keys=True))
        return 0

    if args.command == "research-export":
        report_date = date.fromisoformat(args.date) if args.date else None
        research_input = build_research_input_from_audit_log(
            args.audit_log,
            report_date=report_date,
            max_records=args.max_records,
        )
        output_path = ChatGPTResearchExportWriter(args.output_dir).write(research_input)
        print(json.dumps({"export_path": str(output_path)}, indent=2, sort_keys=True))
        return 0

    if args.command == "exit-matrix":
        scenarios = load_scenarios_from_json(args.scenario_file)
        report = run_exit_matrix(scenarios, initial_equity=args.initial_equity)
        summary_path = report.write_reports(args.output_dir)
        print(
            json.dumps(
                {
                    "summary_path": str(summary_path),
                    "report": report.to_dict(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.command == "collect-qqq-chain":
        now = now_new_york()
        outside_market_hours = (
            now.weekday() >= 5
            or (now.hour, now.minute) < (9, 30)
            or (now.hour, now.minute) > (16, 0)
        )
        if outside_market_hours:
            print(
                json.dumps(
                    {
                        "collected": False,
                        "reason": "outside_regular_market_hours",
                        "checked_at": now.isoformat(),
                        "symbol": "QQQ",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        collector = QqqFullChainCollector(
            source=TastytradeSdkDataSource.from_env(max_contracts=args.max_contracts_per_batch),
            spool_root=args.spool_root,
        )
        result = collector.collect_once("QQQ")
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


def _add_run_arguments(parser: argparse.ArgumentParser, include_loop_arguments: bool) -> None:
    parser.add_argument(
        "--source",
        choices=["mock", "tastytrade"],
        default="mock",
        help="Data source. tastytrade uses real read-only option-chain and DXLink data.",
    )
    parser.add_argument(
        "--symbol",
        default="QQQ",
        help="Underlying symbol to scan.",
    )
    parser.add_argument(
        "--target-dte",
        type=int,
        default=30,
        help="Preferred option expiration DTE.",
    )
    parser.add_argument(
        "--quote-timeout-seconds",
        type=float,
        default=8.0,
        help="Seconds to wait for tastytrade DXLink quote/Greeks events.",
    )
    parser.add_argument(
        "--max-contracts",
        type=int,
        default=120,
        help="Maximum option contracts to subscribe to for one expiration.",
    )
    parser.add_argument(
        "--audit-log",
        default="docs/reports/trade_audit.jsonl",
        help="JSONL audit log path.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=1,
        help="Maximum candidates to dry-run per scan cycle.",
    )
    if include_loop_arguments:
        parser.add_argument(
            "--cycles",
            type=int,
            default=1,
            help="Number of cycles to run. Use 0 to run until stopped.",
        )
        parser.add_argument(
            "--interval-seconds",
            type=float,
            default=60.0,
            help="Seconds to wait between cycles.",
        )


def _account_summary(snapshot) -> dict[str, object]:
    balances = snapshot.balances or {}
    return {
        "connected": snapshot.connected,
        "source": snapshot.source,
        "is_test": snapshot.is_test,
        "account_number_masked": snapshot.account_number_masked,
        "account_type_name": snapshot.account_type_name,
        "margin_or_cash": snapshot.margin_or_cash,
        "net_liquidating_value": balances.get("net_liquidating_value"),
        "cash_balance": balances.get("cash_balance"),
        "derivative_buying_power": balances.get("derivative_buying_power"),
        "positions_count": len(snapshot.positions or []),
        "error_type": snapshot.error_type,
        "message": snapshot.message,
    }


def _add_paper_arguments(parser: argparse.ArgumentParser, include_loop_arguments: bool) -> None:
    parser.add_argument(
        "--source",
        choices=["mock", "tastytrade"],
        default="mock",
        help="Data source for virtual paper trading.",
    )
    parser.add_argument(
        "--symbols",
        default="QQQ",
        help="Comma-separated symbols to scan, for example QQQ,NVDA,SOXL.",
    )
    parser.add_argument("--target-dte", type=int, default=30)
    parser.add_argument("--max-candidates", type=int, default=1)
    parser.add_argument("--starting-equity", type=float, default=2000.0)
    parser.add_argument("--state-path", default=str(DEFAULT_PAPER_STATE_PATH))
    parser.add_argument("--audit-log", default="docs/reports/paper_audit.jsonl")
    parser.add_argument("--quote-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--max-contracts", type=int, default=120)
    parser.add_argument(
        "--strict-spec",
        action="store_true",
        help="Apply the strategy spec compliance gate before virtual paper entries.",
    )
    parser.add_argument(
        "--paper-experimental",
        action="store_true",
        help=(
            "Enable a small paper-only experimental whitelist for strict-spec validation. "
            "This does not affect live trading behavior."
        ),
    )
    if include_loop_arguments:
        parser.add_argument(
            "--cycles",
            type=int,
            default=1,
            help="Number of cycles. Use 0 to run until stopped or --days is reached.",
        )
        parser.add_argument("--interval-seconds", type=float, default=300.0)
        parser.add_argument(
            "--days",
            type=float,
            default=None,
            help="Optional number of days to run, for example 30.",
        )


def _paper_simulator_from_args(args, settings) -> PaperTradingSimulator:
    symbols = tuple(symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip())
    return PaperTradingSimulator(
        settings=settings,
        source=args.source,
        symbols=symbols,
        target_dte=args.target_dte,
        max_candidates_per_symbol=args.max_candidates,
        state_path=args.state_path,
        audit_log_path=args.audit_log,
        starting_equity=args.starting_equity,
        quote_timeout_seconds=args.quote_timeout_seconds,
        max_contracts=args.max_contracts,
        strict_spec=args.strict_spec,
        paper_experimental=args.paper_experimental,
    )
