from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict

from trading_bot.api import UiServerConfig, run_ui_server
from trading_bot.config.settings import load_settings
from trading_bot.runner import DryRunBotRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-bot",
        description="Research-first, dry-run-first options trading assistant.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("config", help="Print the effective bot configuration as JSON.")
    subparsers.add_parser("status", help="Print the current system mode and safety defaults.")

    run_once = subparsers.add_parser("run-once", help="Run one safe dry-run scan cycle.")
    _add_run_arguments(run_once, include_loop_arguments=False)

    run = subparsers.add_parser("run", help="Run repeated safe dry-run scan cycles.")
    _add_run_arguments(run, include_loop_arguments=True)

    ui = subparsers.add_parser("ui", help="Start the local safe dry-run web UI.")
    ui.add_argument("--host", default="127.0.0.1", help="Host interface for the local UI.")
    ui.add_argument("--port", type=int, default=8765, help="Port for the local UI.")
    ui.add_argument(
        "--audit-log",
        default="docs/reports/trade_audit.jsonl",
        help="JSONL audit log path for UI runs.",
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
