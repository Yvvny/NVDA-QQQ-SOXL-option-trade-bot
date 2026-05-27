from __future__ import annotations

import json
from pathlib import Path

from trading_bot.research_bot.analyzer import ResearchInput


def build_chatgpt_markdown_export(research_input: ResearchInput) -> str:
    payload = research_input.to_prompt_payload()
    return "\n".join(
        [
            f"# Trading Bot Research Export - {research_input.report_date}",
            "",
            "## Instructions For ChatGPT",
            "",
            "You are reviewing a research-only, dry-run-first options trading bot. "
            "Analyze the summary below and explain why trades were or were not opened. "
            "Suggest only research hypotheses and backtest tasks. Do not recommend live "
            "trading, increasing position size, removing risk controls, or changing strategy "
            "parameters without backtesting and human review.",
            "",
            "Please return:",
            "",
            "1. Executive summary",
            "2. Data quality findings",
            "3. Main no-trade reasons",
            "4. Strategy observations",
            "5. Risk observations",
            "6. Improvement hypotheses",
            "7. Backtest tasks",
            "8. Recommended next actions",
            "",
            "## Audit Summary",
            "",
            f"- Report date: `{research_input.report_date}`",
            f"- Records read: `{research_input.records_read}`",
            f"- Scan diagnostics: `{research_input.scan_count}`",
            f"- Paper cycles: `{research_input.cycle_count}`",
            f"- Generated candidates: `{research_input.generated_candidates}`",
            f"- Opened paper positions: `{research_input.opened_positions}`",
            f"- Rejected candidates: `{research_input.rejected_candidates}`",
            f"- Market data incomplete count: `{research_input.market_data_incomplete_count}`",
            f"- Symbols: `{', '.join(research_input.symbols) or 'none'}`",
            "",
            "## Top Reason Codes",
            "",
            _markdown_count_table(("reason", "count"), research_input.top_reason_codes),
            "",
            "## Top Liquidity Blocks",
            "",
            _markdown_count_table(("block", "count"), research_input.top_liquidity_blocks),
            "",
            "## Symbol Summaries",
            "",
            _symbol_summaries_markdown(research_input),
            "",
            "## Recent Scan Samples",
            "",
            "```json",
            json.dumps(payload["recent_scan_samples"], indent=2, sort_keys=True),
            "```",
            "",
            "## Full Compact JSON Payload",
            "",
            "```json",
            json.dumps(payload, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )


class ChatGPTResearchExportWriter:
    def __init__(self, output_dir: str | Path = "docs/reports/research") -> None:
        self.output_dir = Path(output_dir)

    def write(self, research_input: ResearchInput) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"chatgpt_export_{research_input.report_date}.md"
        path.write_text(
            build_chatgpt_markdown_export(research_input),
            encoding="utf-8",
        )
        return path


def _markdown_count_table(headers: tuple[str, str], rows: tuple[tuple[str, int], ...]) -> str:
    if not rows:
        return "_None_"
    lines = [
        f"| {headers[0]} | {headers[1]} |",
        "| --- | ---: |",
    ]
    lines.extend(f"| `{name}` | {count} |" for name, count in rows)
    return "\n".join(lines)


def _symbol_summaries_markdown(research_input: ResearchInput) -> str:
    if not research_input.symbol_summaries:
        return "_No symbol diagnostics found._"
    sections: list[str] = []
    for summary in research_input.symbol_summaries:
        sections.extend(
            [
                f"### {summary['symbol']}",
                "",
                f"- Scans: `{summary['scans']}`",
                f"- Avg contracts received: `{summary['avg_contracts_received']}`",
                f"- Avg eligible contracts: `{summary['avg_eligible_contracts']}`",
                f"- Avg received option quotes: `{summary['avg_received_option_quotes']}`",
                f"- Avg received Greeks: `{summary['avg_received_greeks']}`",
                f"- Market data incomplete count: `{summary['market_data_incomplete_count']}`",
                "",
                "Top reasons:",
                _markdown_count_table(("reason", "count"), tuple(summary["top_reasons"])),
                "",
                "Top liquidity blocks:",
                _markdown_count_table(
                    ("block", "count"),
                    tuple(summary["top_liquidity_blocks"]),
                ),
                "",
                "Strategy checks:",
                _strategy_table(summary["strategies"]),
                "",
            ]
        )
    return "\n".join(sections)


def _strategy_table(strategies: list[dict]) -> str:
    if not strategies:
        return "_None_"
    lines = [
        "| strategy | checks | avg score | candidates | top reasons |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for strategy in strategies:
        top_reasons = ", ".join(
            f"{name}={count}" for name, count in strategy.get("top_reasons", [])
        )
        lines.append(
            "| `{}` | {} | {} | {} | {} |".format(
                strategy["strategy_name"],
                strategy["checks"],
                strategy["avg_score"],
                strategy["candidate_generated_count"],
                top_reasons or "none",
            )
        )
    return "\n".join(lines)
