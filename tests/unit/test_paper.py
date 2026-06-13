import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from trading_bot.cli import main
from trading_bot.config.settings import load_settings
from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import (
    ExitPlan,
    OptionContract,
    OptionLeg,
    StrategyCandidate,
    UnderlyingQuote,
)
from trading_bot.data.tastytrade_source import TastytradeMarketSnapshot
from trading_bot.paper import (
    REASON_CAPITAL_GATE_AVAILABLE_CASH_BUFFER_EXCEEDED,
    REASON_CAPITAL_GATE_DRAWDOWN_MODE_DAILY_TRADE_LIMIT,
    REASON_CAPITAL_GATE_PER_TRADE_MAX_LOSS_EXCEEDED,
    REASON_CAPITAL_GATE_SAME_SYMBOL_POSITION_EXISTS,
    REASON_CAPITAL_GATE_SAME_SYMBOL_SAME_DIRECTION_EXISTS,
    REASON_CAPITAL_GATE_TOTAL_OPEN_MAX_LOSS_EXCEEDED,
    PaperAccountState,
    PaperMarkSnapshot,
    PaperPosition,
    PaperTradingSimulator,
    _exit_reason,
    _paper_position_from_candidate,
    paper_capital_preservation_gate,
)
from trading_bot.risk.portfolio import PortfolioState


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
    assert state.to_summary()["available_cash"] == (
        2000 - state.to_summary()["total_open_max_loss"]
    )


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
    settings = load_settings(env={})
    settings = replace(
        settings,
        strategy=replace(settings.strategy, credit_spread_min_planned_reward_risk=0.30),
    )
    simulator = PaperTradingSimulator(
        settings=settings,
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


def test_paper_mode_records_exit_plan_quality_monitor_event(tmp_path):
    state_path = tmp_path / "paper.json"
    audit_path = tmp_path / "paper.jsonl"
    simulator = PaperTradingSimulator(
        source="mock",
        state_path=state_path,
        audit_log_path=audit_path,
        starting_equity=2000,
    )

    result = simulator.run_once()
    audit_records = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    exit_quality_records = [
        record
        for record in audit_records
        if record.get("event_type") == "paper_candidate_exit_plan_quality"
    ]

    assert result.opened_positions == 1
    assert exit_quality_records
    quality = exit_quality_records[0]["exit_plan_quality"]
    assert quality["planned_reward_risk"] is not None
    assert "warning_reasons" in quality


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
        settings=_lenient_credit_test_settings(),
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
        settings=_lenient_credit_test_settings(),
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


def test_paper_capital_gate_rejects_excessive_per_trade_risk():
    decision = paper_capital_preservation_gate(
        _paper_candidate(max_loss=101),
        PaperAccountState(starting_equity=2000),
        PortfolioState(account_equity=2000),
        load_settings(env={}),
    )

    assert decision.approved is False
    assert REASON_CAPITAL_GATE_PER_TRADE_MAX_LOSS_EXCEEDED in decision.reason_codes


def test_paper_capital_gate_rejects_excessive_total_open_risk():
    state = PaperAccountState(
        starting_equity=2000,
        open_positions=(_paper_position("SPY", "put_credit_spread", max_loss=295),),
    )

    decision = paper_capital_preservation_gate(
        _paper_candidate(max_loss=10),
        state,
        PortfolioState(account_equity=2000),
        load_settings(env={}),
    )

    assert decision.approved is False
    assert REASON_CAPITAL_GATE_TOTAL_OPEN_MAX_LOSS_EXCEEDED in decision.reason_codes


def test_paper_capital_gate_uses_available_cash_and_remaining_risk_budget():
    state = PaperAccountState(
        starting_equity=2000,
        realized_pnl=-294.5,
        open_positions=(
            _paper_position("NVDA", "call_debit_spread", max_loss=327.5),
            _paper_position("NVDA", "call_debit_spread", max_loss=180.0),
        ),
    )

    decision = paper_capital_preservation_gate(
        _paper_candidate(underlying="QQQ", max_loss=100),
        state,
        PortfolioState(account_equity=state.equity),
        load_settings(env={}),
    )

    assert decision.approved is False
    assert REASON_CAPITAL_GATE_TOTAL_OPEN_MAX_LOSS_EXCEEDED in decision.reason_codes


def test_paper_capital_gate_can_veto_for_cash_buffer_capacity():
    settings = load_settings(env={})
    settings = replace(
        settings,
        risk=replace(settings.risk, min_account_cash_buffer_pct=0.99),
    )

    decision = paper_capital_preservation_gate(
        _paper_candidate(max_loss=50),
        PaperAccountState(starting_equity=2000),
        PortfolioState(account_equity=2000),
        settings,
    )

    assert decision.approved is False
    assert REASON_CAPITAL_GATE_AVAILABLE_CASH_BUFFER_EXCEEDED in decision.reason_codes


def test_paper_capital_gate_rejects_same_symbol_position():
    state = PaperAccountState(
        starting_equity=2000,
        open_positions=(_paper_position("QQQ", "put_credit_spread", max_loss=50),),
    )

    decision = paper_capital_preservation_gate(
        _paper_candidate(max_loss=50),
        state,
        PortfolioState(account_equity=2000),
        load_settings(env={}),
    )

    assert decision.approved is False
    assert REASON_CAPITAL_GATE_SAME_SYMBOL_POSITION_EXISTS in decision.reason_codes


def test_paper_capital_gate_rejects_same_symbol_same_direction_when_symbol_cap_allows_more():
    settings = load_settings(env={})
    settings = replace(
        settings,
        risk=replace(
            settings.risk,
            paper_capital_gate_max_same_symbol_open_positions=2,
            paper_capital_gate_max_same_symbol_same_direction_positions=1,
        ),
    )
    state = PaperAccountState(
        starting_equity=2000,
        open_positions=(_paper_position("QQQ", "put_credit_spread", max_loss=50),),
    )

    decision = paper_capital_preservation_gate(
        _paper_candidate(strategy_name="call_debit_spread", max_loss=50),
        state,
        PortfolioState(account_equity=2000),
        settings,
    )

    assert decision.approved is False
    assert REASON_CAPITAL_GATE_SAME_SYMBOL_SAME_DIRECTION_EXISTS in decision.reason_codes
    assert REASON_CAPITAL_GATE_SAME_SYMBOL_POSITION_EXISTS not in decision.reason_codes


def test_paper_capital_gate_rejects_second_trade_in_drawdown_mode():
    state = PaperAccountState(starting_equity=2000, realized_pnl=-10)

    decision = paper_capital_preservation_gate(
        _paper_candidate(max_loss=50),
        state,
        PortfolioState(account_equity=1990, new_trades_today=1),
        load_settings(env={}),
    )

    assert decision.approved is False
    assert REASON_CAPITAL_GATE_DRAWDOWN_MODE_DAILY_TRADE_LIMIT in decision.reason_codes


def test_credit_spread_new_position_stores_drawdown_tightened_stop_amounts():
    settings = load_settings(env={})
    state = PaperAccountState(starting_equity=2000, realized_pnl=-294.5)
    candidate = _paper_candidate(max_loss=85)
    candidate = replace(
        candidate,
        max_profit=15,
        expected_credit_or_debit=15,
        exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.0),
    )

    position = _paper_position_from_candidate(
        candidate,
        "credit",
        state=state,
        settings=settings,
    )

    assert position.planned_stop_loss_amount == 25.5
    assert position.hard_stop_loss_amount == 46.75
    assert position.stop_loss_basis == "credit_spread_credit_multiple_pnl_loss"


def test_new_credit_spread_stores_planned_profit_target_amount():
    candidate = replace(
        _paper_candidate(max_loss=85),
        max_profit=15,
        expected_credit_or_debit=15,
        exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.0),
    )

    position = _paper_position_from_candidate(
        candidate,
        "credit",
        state=PaperAccountState(starting_equity=2000),
        settings=load_settings(env={}),
    )

    assert position.planned_profit_target_amount == 7.5


def test_new_debit_spread_caps_planned_profit_target_by_debit_return():
    candidate = replace(
        _paper_candidate(strategy_name="call_debit_spread", max_loss=327.5),
        max_profit=672.5,
        expected_credit_or_debit=327.5,
        exit_plan=ExitPlan(profit_target_pct=0.75, stop_loss_pct=0.45),
    )

    position = _paper_position_from_candidate(
        candidate,
        "debit",
        state=PaperAccountState(starting_equity=2000),
        settings=load_settings(env={}),
    )

    assert position.planned_profit_target_amount == 196.5


def test_paper_exit_prefers_stored_planned_profit_target_amount():
    position = replace(
        _paper_position("QQQ", "call_debit_spread", max_loss=180),
        max_profit=320,
        expected_credit_or_debit=180,
        unrealized_pnl=109,
        planned_profit_target_amount=108,
        exit_plan={"profit_target_pct": 0.75, "stop_loss_pct": 0.45},
    )

    assert _exit_reason(position, settings=load_settings(env={})) == "profit_target"


def test_credit_spread_triggers_normalized_planned_stop():
    position = replace(
        _paper_position("QQQ", "put_credit_spread", max_loss=85),
        max_profit=15,
        expected_credit_or_debit=15,
        unrealized_pnl=-30,
        planned_stop_loss_amount=30,
        hard_stop_loss_amount=46.75,
    )

    assert _exit_reason(position, settings=load_settings(env={})) == "stop_loss_multiple"


def test_credit_spread_triggers_hard_stop_before_planned_stop_reason():
    position = replace(
        _paper_position("QQQ", "put_credit_spread", max_loss=85),
        max_profit=15,
        expected_credit_or_debit=15,
        unrealized_pnl=-46.75,
        planned_stop_loss_amount=30,
        hard_stop_loss_amount=46.75,
    )

    assert _exit_reason(position, settings=load_settings(env={})) == "hard_stop_loss"


def test_credit_spread_triggers_eod_tightened_stop():
    checked_at = datetime(2026, 6, 4, 15, 45)
    position = replace(
        _paper_position("QQQ", "put_credit_spread", max_loss=85),
        max_profit=15,
        expected_credit_or_debit=15,
        unrealized_pnl=-24,
        planned_stop_loss_amount=30,
        hard_stop_loss_amount=46.75,
    )

    assert (
        _exit_reason(position, settings=load_settings(env={}), checked_at=checked_at)
        == "eod_tightened_stop_loss"
    )


def test_debit_spread_triggers_hard_stop_loss():
    position = replace(
        _paper_position("QQQ", "call_debit_spread", max_loss=180),
        max_profit=320,
        expected_credit_or_debit=180,
        unrealized_pnl=-81,
        planned_stop_loss_amount=63,
        hard_stop_loss_amount=81,
        exit_plan={"profit_target_pct": 0.75, "stop_loss_pct": 0.45},
    )

    assert _exit_reason(position, settings=load_settings(env={})) == "hard_stop_loss"


def test_debit_warning_stop_does_not_trigger_when_market_data_missing():
    position = replace(
        _paper_position("QQQ", "call_debit_spread", max_loss=180),
        max_profit=320,
        expected_credit_or_debit=180,
        unrealized_pnl=-63,
        planned_stop_loss_amount=63,
        hard_stop_loss_amount=81,
        exit_plan={"profit_target_pct": 0.75, "stop_loss_pct": 0.45},
        path_snapshots=(
            PaperMarkSnapshot(date="2026-06-04", dte=21, mark_price=1.0),
            PaperMarkSnapshot(date="2026-06-05", dte=20, mark_price=1.1),
        ),
    )

    assert _exit_reason(position, settings=load_settings(env={})) is None


def test_debit_warning_stop_triggers_when_thesis_invalidated_for_two_snapshots():
    position = replace(
        _paper_position("QQQ", "call_debit_spread", max_loss=180),
        max_profit=320,
        expected_credit_or_debit=180,
        unrealized_pnl=-63,
        planned_stop_loss_amount=63,
        hard_stop_loss_amount=81,
        exit_plan={"profit_target_pct": 0.75, "stop_loss_pct": 0.45},
        path_snapshots=(
            PaperMarkSnapshot(
                date="2026-06-04",
                dte=21,
                mark_price=1.0,
                underlying_price=99,
                vwap=100,
                price_action_confirmed=False,
            ),
            PaperMarkSnapshot(
                date="2026-06-05",
                dte=20,
                mark_price=1.1,
                underlying_price=98,
                vwap=100,
                price_action_confirmed=False,
            ),
        ),
    )

    assert (
        _exit_reason(position, settings=load_settings(env={}))
        == "thesis_invalidated_stop_loss"
    )


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


def _paper_candidate(
    *,
    strategy_name: str = "put_credit_spread",
    underlying: str = "QQQ",
    max_loss: float | None = 50.0,
) -> StrategyCandidate:
    expiration = date(2026, 6, 19)
    return StrategyCandidate(
        strategy_name=strategy_name,
        underlying=underlying,
        legs=(
            OptionLeg(
                contract=OptionContract(
                    symbol=f"{underlying} {expiration.isoformat()} 450 put",
                    underlying=underlying,
                    expiration=expiration,
                    strike=450,
                    option_type=OptionType.PUT,
                    bid=1.0,
                    ask=1.1,
                    mid=1.05,
                    delta=-0.25,
                    volume=100,
                    open_interest=1000,
                ),
                action=OptionAction.SELL,
            ),
            OptionLeg(
                contract=OptionContract(
                    symbol=f"{underlying} {expiration.isoformat()} 449 put",
                    underlying=underlying,
                    expiration=expiration,
                    strike=449,
                    option_type=OptionType.PUT,
                    bid=0.5,
                    ask=0.6,
                    mid=0.55,
                    delta=-0.10,
                    volume=100,
                    open_interest=1000,
                ),
                action=OptionAction.BUY,
            ),
        ),
        dte=30,
        entry_score=75,
        max_profit=50,
        max_loss=max_loss,
        expected_credit_or_debit=50,
        reason_codes=("fixture",),
        exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.5),
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
        liquidity=replace(
            settings.liquidity,
            max_package_bid_ask_pct_of_entry=0.20,
        ),
        selection=replace(settings.selection, enabled=False),
    )


def _paper_position(
    symbol: str,
    strategy_name: str,
    *,
    max_loss: float,
) -> PaperPosition:
    return PaperPosition(
        position_id=f"{symbol}-{strategy_name}",
        opened_at="2026-06-06T10:00:00-04:00",
        underlying=symbol,
        strategy_name=strategy_name,
        dte_at_entry=30,
        entry_score=75,
        max_profit=50,
        max_loss=max_loss,
        expected_credit_or_debit=50,
        price_effect="credit",
        entry_value=-50,
        legs=(),
        exit_plan={},
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
