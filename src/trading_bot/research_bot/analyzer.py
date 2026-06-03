from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date
from pathlib import Path
from typing import Any

from trading_bot.core.time_utils import NEW_YORK_TIME_ZONE, parse_timestamp, today_new_york


@dataclass(frozen=True)
class ResearchInput:
    report_date: str
    records_read: int
    scan_count: int
    cycle_count: int
    opened_positions: int
    rejected_candidates: int
    generated_candidates: int
    symbols: tuple[str, ...]
    top_reason_codes: tuple[tuple[str, int], ...]
    top_liquidity_blocks: tuple[tuple[str, int], ...]
    market_data_incomplete_count: int
    symbol_summaries: tuple[dict[str, Any], ...]
    recent_scan_samples: tuple[dict[str, Any], ...]

    def to_prompt_payload(self) -> dict[str, Any]:
        return {
            "report_date": self.report_date,
            "records_read": self.records_read,
            "scan_count": self.scan_count,
            "cycle_count": self.cycle_count,
            "opened_positions": self.opened_positions,
            "rejected_candidates": self.rejected_candidates,
            "generated_candidates": self.generated_candidates,
            "symbols": list(self.symbols),
            "top_reason_codes": [list(item) for item in self.top_reason_codes],
            "top_liquidity_blocks": [list(item) for item in self.top_liquidity_blocks],
            "market_data_incomplete_count": self.market_data_incomplete_count,
            "symbol_summaries": list(self.symbol_summaries),
            "recent_scan_samples": list(self.recent_scan_samples),
        }


def build_research_input_from_audit_log(
    audit_log_path: str | Path,
    *,
    report_date: date | None = None,
    max_records: int = 1000,
) -> ResearchInput:
    report_date = report_date or today_new_york()
    records = _read_jsonl(Path(audit_log_path), max_records=max_records)
    filtered = [
        record
        for record in records
        if _record_date(record) is None or _record_date(record) == report_date
    ]
    scan_records = [
        record for record in filtered if record.get("event_type") == "paper_scan_diagnostics"
    ]
    cycle_records = [record for record in filtered if record.get("event_type") == "paper_cycle"]

    reason_counts: Counter[str] = Counter()
    liquidity_counts: Counter[str] = Counter()
    symbol_data: dict[str, dict[str, Any]] = defaultdict(_empty_symbol_summary)
    market_data_incomplete_count = 0

    for record in scan_records:
        diagnostics = record.get("diagnostics", {})
        if not isinstance(diagnostics, dict):
            continue
        symbol = str(diagnostics.get("symbol") or "UNKNOWN")
        summary = symbol_data[symbol]
        summary["symbol"] = symbol
        summary["scans"] += 1

        contracts = diagnostics.get("contracts", {})
        if isinstance(contracts, dict):
            summary["contracts_received_total"] += int(contracts.get("received") or 0)
            summary["eligible_contracts_total"] += int(contracts.get("eligible") or 0)

        market_data = diagnostics.get("market_data", {})
        if isinstance(market_data, dict):
            if market_data.get("market_data_incomplete") is True:
                market_data_incomplete_count += 1
                summary["market_data_incomplete_count"] += 1
            summary["received_option_quotes_total"] += int(
                market_data.get("received_option_quotes") or 0
            )
            summary["received_greeks_total"] += int(market_data.get("received_greeks") or 0)

        reason_codes = diagnostics.get("reason_codes", [])
        if isinstance(reason_codes, list):
            reason_counts.update(reason for reason in reason_codes if isinstance(reason, str))
            summary["reason_counts"].update(
                reason for reason in reason_codes if isinstance(reason, str)
            )

        liquidity_blocks = diagnostics.get("liquidity_blocks", {})
        if isinstance(liquidity_blocks, dict):
            for reason, count in liquidity_blocks.items():
                numeric_count = int(count or 0)
                liquidity_counts[str(reason)] += numeric_count
                summary["liquidity_counts"][str(reason)] += numeric_count

        strategies = diagnostics.get("strategies", [])
        if isinstance(strategies, list):
            for strategy in strategies:
                if not isinstance(strategy, dict):
                    continue
                name = str(strategy.get("strategy_name") or "unknown")
                strategy_summary = summary["strategies"][name]
                strategy_summary["strategy_name"] = name
                strategy_summary["checks"] += 1
                strategy_summary["score_total"] += float(strategy.get("score") or 0.0)
                if strategy.get("candidate_generated") is True:
                    strategy_summary["candidate_generated_count"] += 1
                reasons = strategy.get("reason_codes", [])
                if isinstance(reasons, list):
                    strategy_summary["reason_counts"].update(
                        reason for reason in reasons if isinstance(reason, str)
                    )

    opened_positions = _sum_cycle_field(cycle_records, "opened_positions")
    rejected_candidates = _sum_cycle_field(cycle_records, "rejected_candidates")
    generated_candidates = _sum_cycle_field(cycle_records, "generated_candidates")

    return ResearchInput(
        report_date=report_date.isoformat(),
        records_read=len(filtered),
        scan_count=len(scan_records),
        cycle_count=len(cycle_records),
        opened_positions=opened_positions,
        rejected_candidates=rejected_candidates,
        generated_candidates=generated_candidates,
        symbols=tuple(sorted(symbol_data)),
        top_reason_codes=tuple(reason_counts.most_common(10)),
        top_liquidity_blocks=tuple(liquidity_counts.most_common(10)),
        market_data_incomplete_count=market_data_incomplete_count,
        symbol_summaries=tuple(_finalize_symbol_summary(item) for item in symbol_data.values()),
        recent_scan_samples=tuple(_scan_sample(record) for record in scan_records[-5:]),
    )


def _read_jsonl(path: Path, *, max_records: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    for line in lines[-max_records:]:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _record_date(record: dict[str, Any]) -> date | None:
    logged_at = record.get("logged_at")
    if not isinstance(logged_at, str):
        return None
    timestamp = parse_timestamp(logged_at, naive_timezone=UTC)
    if timestamp is None:
        return None
    return timestamp.astimezone(NEW_YORK_TIME_ZONE).date()


def _empty_symbol_summary() -> dict[str, Any]:
    return {
        "symbol": "",
        "scans": 0,
        "contracts_received_total": 0,
        "eligible_contracts_total": 0,
        "received_option_quotes_total": 0,
        "received_greeks_total": 0,
        "market_data_incomplete_count": 0,
        "reason_counts": Counter(),
        "liquidity_counts": Counter(),
        "strategies": defaultdict(_empty_strategy_summary),
    }


def _empty_strategy_summary() -> dict[str, Any]:
    return {
        "strategy_name": "",
        "checks": 0,
        "score_total": 0.0,
        "candidate_generated_count": 0,
        "reason_counts": Counter(),
    }


def _finalize_symbol_summary(summary: dict[str, Any]) -> dict[str, Any]:
    scans = max(1, int(summary["scans"]))
    return {
        "symbol": summary["symbol"],
        "scans": summary["scans"],
        "avg_contracts_received": round(summary["contracts_received_total"] / scans, 2),
        "avg_eligible_contracts": round(summary["eligible_contracts_total"] / scans, 2),
        "avg_received_option_quotes": round(
            summary["received_option_quotes_total"] / scans,
            2,
        ),
        "avg_received_greeks": round(summary["received_greeks_total"] / scans, 2),
        "market_data_incomplete_count": summary["market_data_incomplete_count"],
        "top_reasons": summary["reason_counts"].most_common(5),
        "top_liquidity_blocks": summary["liquidity_counts"].most_common(5),
        "strategies": [
            _finalize_strategy_summary(strategy) for strategy in summary["strategies"].values()
        ],
    }


def _finalize_strategy_summary(summary: dict[str, Any]) -> dict[str, Any]:
    checks = max(1, int(summary["checks"]))
    return {
        "strategy_name": summary["strategy_name"],
        "checks": summary["checks"],
        "avg_score": round(summary["score_total"] / checks, 2),
        "candidate_generated_count": summary["candidate_generated_count"],
        "top_reasons": summary["reason_counts"].most_common(5),
    }


def _sum_cycle_field(records: list[dict[str, Any]], field_name: str) -> int:
    total = 0
    for record in records:
        result = record.get("result", {})
        if isinstance(result, dict):
            total += int(result.get(field_name) or 0)
    return total


def _scan_sample(record: dict[str, Any]) -> dict[str, Any]:
    diagnostics = record.get("diagnostics", {})
    if not isinstance(diagnostics, dict):
        return {}
    return {
        "logged_at": record.get("logged_at"),
        "symbol": diagnostics.get("symbol"),
        "underlying_last": diagnostics.get("underlying_last"),
        "contracts": diagnostics.get("contracts"),
        "market_data": diagnostics.get("market_data"),
        "reason_codes": diagnostics.get("reason_codes"),
    }
