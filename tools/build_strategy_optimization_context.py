from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT_DIR = Path("docs/reports/strategy_optimization")

STRATEGY_FILES = [
    "src/trading_bot/config/risk_limits.yaml",
    "src/trading_bot/config/settings.py",
    "src/trading_bot/strategies/scoring.py",
    "src/trading_bot/strategies/selector.py",
    "src/trading_bot/strategies/short_premium.py",
    "src/trading_bot/strategies/trend_participation.py",
    "src/trading_bot/strategies/timing_filters.py",
    "src/trading_bot/strategies/spec_compliance.py",
    "src/trading_bot/risk/engine.py",
    "src/trading_bot/risk/sizing.py",
]

ROUND_TOPICS = [
    "Overall strategy diagnosis and highest-leverage bottleneck",
    "Entry timing gate refinement for debit spreads",
    "Price action confirmation definition",
    "QQQ put credit spread selection quality",
    "NVDA debit spread retention versus removal",
    "Reward/risk filter thresholds",
    "Exit plan comparison",
    "Stop-loss rule improvement",
    "Profit target rule improvement",
    "Position sizing and capital scaling",
    "Available-cash risk budget validation",
    "Symbol allocation and experimental budget",
    "Regime classifier improvement",
    "Liquidity and missing volume/OI handling",
    "Candidate ranking and opportunity selection",
    "Duplicate/correlated position prevention",
    "Strategy attribution metrics",
    "Paper-trade data collection for future RL filter",
    "Final vNext strategy proposal",
    "Audit prior 19 rounds for conflicts, overfitting, and safety regressions",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-key", default=r"C:\Users\26560\.ssh\oci_trading_bot.key")
    parser.add_argument("--remote", default="ubuntu@137.131.60.215")
    parser.add_argument("--remote-root", default="/opt/trading-bot")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    rounds_dir = output_dir / "rounds"
    rounds_dir.mkdir(parents=True, exist_ok=True)

    remote_summary = _fetch_remote_summary(args.ssh_key, args.remote, args.remote_root)
    local_strategy = _local_strategy_snapshot()
    context = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "remote": {"host": args.remote, "root": args.remote_root},
        "paper_summary": remote_summary,
        "local_strategy": local_strategy,
        "safety_constraints": {
            "live_trading": "disabled by default",
            "llm_changes": (
                "ChatGPT may propose changes, Codex must validate and test "
                "before code changes"
            ),
            "risk_engine": "must retain veto power",
            "forbidden": [
                "0DTE",
                "naked options",
                "undefined-risk orders",
                "market orders for options",
                "missing max-loss",
                "missing exit plan",
            ],
        },
    }

    (output_dir / "current_context.json").write_text(
        json.dumps(context, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_dir / "current_context.md").write_text(
        _context_markdown(context),
        encoding="utf-8",
    )
    (output_dir / "trade_history_summary.md").write_text(
        _trade_history_markdown(remote_summary),
        encoding="utf-8",
    )
    _write_round_prompts(output_dir, rounds_dir, context)
    return 0


def _fetch_remote_summary(ssh_key: str, remote: str, remote_root: str) -> dict[str, Any]:
    script = r"""
import json
from collections import Counter, defaultdict
from pathlib import Path

state_path = Path("docs/reports/paper_account.json")
audit_path = Path("docs/reports/paper_audit.jsonl")
paths_path = Path("docs/reports/paper_position_paths.jsonl")

state = json.loads(state_path.read_text()) if state_path.exists() else {}
event_counts = Counter()
strategy_events = Counter()
strategy_rejections = Counter()
scan_reasons = Counter()
spec_reasons = Counter()
risk_reasons = Counter()
liquidity_reasons = Counter()
cycle_summaries = []
opened_candidates = []
rejected_candidates = []

if audit_path.exists():
    for line in audit_path.read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = str(record.get("event_type") or "")
        event_counts[event_type] += 1
        candidate = record.get("candidate") if isinstance(record.get("candidate"), dict) else {}
        strategy = candidate.get("strategy_name") or record.get("strategy_name")
        if strategy:
            strategy_events[strategy] += 1
        if event_type == "paper_cycle":
            result = record.get("result") if isinstance(record.get("result"), dict) else {}
            cycle_summaries.append({
                "logged_at": record.get("logged_at"),
                "cycle_index": result.get("cycle_index"),
                "generated_candidates": result.get("generated_candidates"),
                "opened_positions": result.get("opened_positions"),
                "rejected_candidates": result.get("rejected_candidates"),
                "closed_positions": result.get("closed_positions"),
                "summary": result.get("summary"),
                "errors": result.get("errors"),
            })
        elif event_type == "paper_position_opened":
            opened_candidates.append({
                "logged_at": record.get("logged_at"),
                "symbol": record.get("symbol") or candidate.get("underlying"),
                "strategy": candidate.get("strategy_name"),
                "entry_score": candidate.get("entry_score"),
                "max_loss": candidate.get("max_loss"),
                "max_profit": candidate.get("max_profit"),
                "expected_credit_or_debit": candidate.get("expected_credit_or_debit"),
                "reason_codes": candidate.get("reason_codes"),
            })
        elif event_type in {"paper_candidate_rejected", "paper_candidate_spec_rejected"}:
            reasons = record.get("spec_reason_codes")
            if reasons is None:
                risk = (
                    record.get("risk_decision")
                    if isinstance(record.get("risk_decision"), dict)
                    else {}
                )
                reasons = risk.get("reason_codes") or []
            rejected_candidates.append({
                "logged_at": record.get("logged_at"),
                "symbol": record.get("symbol") or candidate.get("underlying"),
                "strategy": candidate.get("strategy_name"),
                "entry_score": candidate.get("entry_score"),
                "max_loss": candidate.get("max_loss"),
                "reasons": reasons,
            })
            strategy_rejections[candidate.get("strategy_name") or "unknown"] += 1
            if event_type == "paper_candidate_spec_rejected":
                spec_reasons.update(reasons or [])
            else:
                risk_reasons.update(reasons or [])
        elif event_type == "paper_scan_diagnostics":
            diagnostics = (
                record.get("diagnostics")
                if isinstance(record.get("diagnostics"), dict)
                else {}
            )
            scan_reasons.update(diagnostics.get("reason_codes") or [])
            liquidity_reasons.update((diagnostics.get("liquidity_blocks") or {}).keys())
            for item in diagnostics.get("strategies") or []:
                if not isinstance(item, dict):
                    continue
                for reason in item.get("reason_codes") or []:
                    scan_reasons[reason] += 1

path_counts = Counter()
if paths_path.exists():
    for line in paths_path.read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        pid = record.get("position_id")
        if pid:
            path_counts[pid] += 1

open_positions = state.get("open_positions") or []
closed_trades = state.get("closed_trades") or []

def compact_position(pos):
    return {
        "position_id": pos.get("position_id"),
        "opened_at": pos.get("opened_at"),
        "underlying": pos.get("underlying"),
        "strategy_name": pos.get("strategy_name"),
        "entry_score": pos.get("entry_score"),
        "max_profit": pos.get("max_profit"),
        "max_loss": pos.get("max_loss"),
        "expected_credit_or_debit": pos.get("expected_credit_or_debit"),
        "price_effect": pos.get("price_effect"),
        "entry_value": pos.get("entry_value"),
        "last_mark_value": pos.get("last_mark_value"),
        "unrealized_pnl": pos.get("unrealized_pnl"),
        "last_marked_at": pos.get("last_marked_at"),
        "legs": pos.get("legs"),
        "exit_plan": pos.get("exit_plan"),
        "path_snapshot_count": path_counts.get(pos.get("position_id"), 0),
    }

def compact_closed(trade):
    pos = trade.get("position") or {}
    return {
        "closed_at": trade.get("closed_at"),
        "exit_reason": trade.get("exit_reason"),
        "realized_pnl": trade.get("realized_pnl"),
        "position": compact_position(pos),
    }

latest_summary = (cycle_summaries[-1].get("summary") if cycle_summaries else None) or {}
payload = {
    "state": {
        "starting_equity": state.get("starting_equity"),
        "realized_pnl": state.get("realized_pnl"),
        "unrealized_pnl": latest_summary.get("unrealized_pnl"),
        "equity": latest_summary.get("equity"),
        "available_cash": latest_summary.get("available_cash"),
        "total_open_max_loss": latest_summary.get("total_open_max_loss"),
        "total_pnl": latest_summary.get("total_pnl"),
        "total_return_pct": latest_summary.get("total_return_pct"),
        "open_positions": len(open_positions),
        "closed_trades": len(closed_trades),
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
    },
    "open_positions": [compact_position(pos) for pos in open_positions],
    "closed_trades": [compact_closed(trade) for trade in closed_trades],
    "opened_candidates": opened_candidates[-50:],
    "rejected_candidates": rejected_candidates[-100:],
    "latest_cycles": cycle_summaries[-20:],
    "event_counts": dict(event_counts.most_common()),
    "strategy_events": dict(strategy_events.most_common()),
    "strategy_rejections": dict(strategy_rejections.most_common()),
    "top_scan_reasons": dict(scan_reasons.most_common(30)),
    "top_spec_reasons": dict(spec_reasons.most_common(20)),
    "top_risk_reasons": dict(risk_reasons.most_common(20)),
    "top_liquidity_reasons": dict(liquidity_reasons.most_common(20)),
}
print(json.dumps(payload, sort_keys=True))
"""
    command = f"cd {remote_root} && .venv/bin/python - <<'PY'\n{script}\nPY"
    result = subprocess.run(
        ["ssh", "-i", ssh_key, remote, command],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _local_strategy_snapshot() -> dict[str, Any]:
    files = {}
    for path_text in STRATEGY_FILES:
        path = Path(path_text)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        files[path_text] = {
            "size": len(text),
            "content": text[:12000],
            "truncated": len(text) > 12000,
        }
    return {"files": files}


def _context_markdown(context: dict[str, Any]) -> str:
    summary = context["paper_summary"]["state"]
    positions = context["paper_summary"]["open_positions"]
    closed = context["paper_summary"]["closed_trades"]
    reasons = context["paper_summary"]["top_scan_reasons"]
    return "\n".join(
        [
            "# Strategy Optimization Context",
            "",
            "## Account Snapshot",
            "",
            f"- Starting equity: `{summary.get('starting_equity')}`",
            f"- Current equity: `{summary.get('equity')}`",
            f"- Available cash: `{summary.get('available_cash')}`",
            f"- Total PnL: `{summary.get('total_pnl')}`",
            f"- Total return pct: `{summary.get('total_return_pct')}`",
            f"- Realized PnL: `{summary.get('realized_pnl')}`",
            f"- Unrealized PnL: `{summary.get('unrealized_pnl')}`",
            f"- Total open max loss: `{summary.get('total_open_max_loss')}`",
            f"- Open positions: `{summary.get('open_positions')}`",
            f"- Closed trades: `{summary.get('closed_trades')}`",
            f"- Created at: `{summary.get('created_at')}`",
            f"- Updated at: `{summary.get('updated_at')}`",
            "",
            "## Open Positions",
            "",
            _markdown_table(
                positions,
                [
                    "position_id",
                    "opened_at",
                    "underlying",
                    "strategy_name",
                    "entry_score",
                    "max_loss",
                    "max_profit",
                    "unrealized_pnl",
                ],
            ),
            "",
            "## Closed Trades",
            "",
            _markdown_table(
                [
                    {
                        "closed_at": item.get("closed_at"),
                        "exit_reason": item.get("exit_reason"),
                        "realized_pnl": item.get("realized_pnl"),
                        "underlying": (item.get("position") or {}).get("underlying"),
                        "strategy_name": (item.get("position") or {}).get("strategy_name"),
                        "opened_at": (item.get("position") or {}).get("opened_at"),
                        "max_loss": (item.get("position") or {}).get("max_loss"),
                    }
                    for item in closed
                ],
                [
                    "closed_at",
                    "underlying",
                    "strategy_name",
                    "opened_at",
                    "exit_reason",
                    "realized_pnl",
                    "max_loss",
                ],
            ),
            "",
            "## Top Current Bottlenecks",
            "",
            _markdown_counter(reasons),
            "",
            "## Safety Constraints For ChatGPT",
            "",
            "- Do not propose live trading enablement.",
            "- Do not remove defined-risk, 0DTE, max-loss, liquidity, or risk-engine checks.",
            (
                "- Any proposed strategy change must include expected benefit, risk impact, "
                "required tests, and whether it is safe for paper-only deployment."
            ),
            "- ChatGPT suggestions are advisory only; Codex must validate, test, and document.",
            "",
        ]
    )


def _trade_history_markdown(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Trade History Summary",
            "",
            "## Event Counts",
            "",
            _markdown_counter(summary.get("event_counts", {})),
            "",
            "## Strategy Events",
            "",
            _markdown_counter(summary.get("strategy_events", {})),
            "",
            "## Strategy Rejections",
            "",
            _markdown_counter(summary.get("strategy_rejections", {})),
            "",
            "## Top Spec Rejection Reasons",
            "",
            _markdown_counter(summary.get("top_spec_reasons", {})),
            "",
            "## Top Risk Rejection Reasons",
            "",
            _markdown_counter(summary.get("top_risk_reasons", {})),
            "",
            "## Recent Opened Candidates",
            "",
            _markdown_table(
                summary.get("opened_candidates", []),
                [
                    "logged_at",
                    "symbol",
                    "strategy",
                    "entry_score",
                    "max_loss",
                    "max_profit",
                    "expected_credit_or_debit",
                ],
            ),
            "",
        ]
    )


def _write_round_prompts(
    output_dir: Path,
    rounds_dir: Path,
    context: dict[str, Any],
) -> None:
    index_path = output_dir / "strategy_optimization_rounds.jsonl"
    if not index_path.exists():
        index_path.write_text("", encoding="utf-8")
    for number, topic in enumerate(ROUND_TOPICS, start=1):
        prompt = _round_prompt(number, topic, context)
        (rounds_dir / f"round_{number:02d}_prompt.md").write_text(prompt, encoding="utf-8")


def _round_prompt(number: int, topic: str, context: dict[str, Any]) -> str:
    state = context["paper_summary"]["state"]
    return f"""# Strategy Optimization Round {number:02d}

Topic: {topic}

You are reviewing a paper-only systematic options bot. Use the provided context files:

- `current_context.md`
- `current_context.json`
- `trade_history_summary.md`

Current account snapshot:

- Starting equity: `{state.get("starting_equity")}`
- Current equity: `{state.get("equity")}`
- Total PnL: `{state.get("total_pnl")}`
- Total return pct: `{state.get("total_return_pct")}`
- Available cash: `{state.get("available_cash")}`
- Open max loss: `{state.get("total_open_max_loss")}`
- Open positions: `{state.get("open_positions")}`
- Closed trades: `{state.get("closed_trades")}`

Hard safety rules:

- Do not enable live trading.
- Do not remove the risk engine.
- Do not allow 0DTE, naked options, undefined-risk trades, market orders,
  missing max loss, or missing exit plan.
- Any recommendation must be paper-only and testable.

Return a concise structured answer with these exact sections:

1. Diagnosis
2. Proposed Change
3. Expected Benefit
4. Risk Impact
5. Required Tests
6. Codex Implementation Instruction
7. Should Deploy To Paper? yes/no

Focus only on this round's topic: {topic}.
"""


def _markdown_counter(values: dict[str, Any]) -> str:
    if not values:
        return "No data."
    return "\n".join(f"- `{key}`: `{value}`" for key, value in values.items())


def _markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "No data."
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(_cell(row.get(column)) for column in columns) + " |")
    return "\n".join([header, separator, *body])


def _cell(value: Any) -> str:
    if value is None:
        return ""
    text = json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
    return text.replace("|", "\\|").replace("\n", " ")[:240]


if __name__ == "__main__":
    raise SystemExit(main())
