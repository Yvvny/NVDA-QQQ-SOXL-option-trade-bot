from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from trading_bot.config.settings import BotSettings, load_settings
from trading_bot.core.enums import OptionAction
from trading_bot.core.models import OptionContract, StrategyCandidate
from trading_bot.data.tastytrade_source import TastytradeSdkDataSource
from trading_bot.execution.order_builder import OrderBuilder
from trading_bot.risk.engine import RiskEngine
from trading_bot.risk.portfolio import OpenPosition, PortfolioState
from trading_bot.runner import (
    DryRunBotRunner,
    _regime_label_for_snapshot,
    _score_inputs_for_snapshot,
)
from trading_bot.storage.audit import JsonlAuditLogger
from trading_bot.strategies.selector import StrategySelector
from trading_bot.strategies.spec_compliance import validate_candidate_against_strategy_spec

DEFAULT_PAPER_STATE_PATH = Path("docs/reports/paper_account.json")
DEFAULT_PAPER_AUDIT_PATH = Path("docs/reports/paper_audit.jsonl")


@dataclass(frozen=True)
class PaperLeg:
    symbol: str
    action: str
    quantity: int
    option_type: str
    strike: float
    expiration: str
    entry_mid: float


@dataclass(frozen=True)
class PaperPosition:
    position_id: str
    opened_at: str
    underlying: str
    strategy_name: str
    dte_at_entry: int
    entry_score: float
    max_profit: float | None
    max_loss: float
    expected_credit_or_debit: float
    price_effect: str
    entry_value: float
    legs: tuple[PaperLeg, ...]
    exit_plan: dict[str, Any]
    last_mark_value: float | None = None
    unrealized_pnl: float = 0.0
    last_marked_at: str | None = None


@dataclass(frozen=True)
class PaperClosedTrade:
    position: PaperPosition
    closed_at: str
    exit_reason: str
    realized_pnl: float


@dataclass(frozen=True)
class PaperAccountState:
    starting_equity: float = 2000.0
    realized_pnl: float = 0.0
    open_positions: tuple[PaperPosition, ...] = field(default_factory=tuple)
    closed_trades: tuple[PaperClosedTrade, ...] = field(default_factory=tuple)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def unrealized_pnl(self) -> float:
        return round(sum(position.unrealized_pnl for position in self.open_positions), 2)

    @property
    def equity(self) -> float:
        return round(self.starting_equity + self.realized_pnl + self.unrealized_pnl, 2)

    @property
    def total_open_max_loss(self) -> float:
        return round(sum(position.max_loss for position in self.open_positions), 2)

    def to_summary(self) -> dict[str, Any]:
        return {
            "starting_equity": self.starting_equity,
            "equity": self.equity,
            "realized_pnl": round(self.realized_pnl, 2),
            "unrealized_pnl": self.unrealized_pnl,
            "total_pnl": round(self.equity - self.starting_equity, 2),
            "total_return_pct": round(
                ((self.equity - self.starting_equity) / self.starting_equity) * 100,
                2,
            ),
            "open_positions": len(self.open_positions),
            "closed_trades": len(self.closed_trades),
            "total_open_max_loss": self.total_open_max_loss,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class PaperCycleResult:
    cycle_index: int
    source: str
    symbols: tuple[str, ...]
    generated_candidates: int
    opened_positions: int
    closed_positions: int
    rejected_candidates: int
    errors: tuple[str, ...]
    state_path: str
    summary: dict[str, Any]
    strict_spec: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PaperTradingSimulator:
    def __init__(
        self,
        *,
        settings: BotSettings | None = None,
        source: str = "mock",
        symbols: tuple[str, ...] = ("QQQ",),
        target_dte: int = 30,
        max_candidates_per_symbol: int = 1,
        state_path: str | Path = DEFAULT_PAPER_STATE_PATH,
        audit_log_path: str | Path = DEFAULT_PAPER_AUDIT_PATH,
        starting_equity: float = 2000.0,
        quote_timeout_seconds: float = 8.0,
        max_contracts: int = 120,
        tastytrade_data_source: TastytradeSdkDataSource | None = None,
        strict_spec: bool = False,
    ) -> None:
        if source not in {"mock", "tastytrade"}:
            raise ValueError("source must be 'mock' or 'tastytrade'.")
        if not symbols:
            raise ValueError("At least one symbol is required.")
        if target_dte <= 0:
            raise ValueError("target_dte must be positive.")
        if max_candidates_per_symbol <= 0:
            raise ValueError("max_candidates_per_symbol must be positive.")
        if starting_equity <= 0:
            raise ValueError("starting_equity must be positive.")

        self.settings = settings or load_settings()
        self.source = source
        self.symbols = tuple(symbol.upper() for symbol in symbols)
        self.target_dte = target_dte
        self.max_candidates_per_symbol = max_candidates_per_symbol
        self.state_path = Path(state_path)
        self.audit_logger = JsonlAuditLogger(audit_log_path)
        self.starting_equity = starting_equity
        self.quote_timeout_seconds = quote_timeout_seconds
        self.max_contracts = max_contracts
        self.tastytrade_data_source = tastytrade_data_source
        self.strict_spec = strict_spec
        self.selector = StrategySelector(self.settings)
        self.risk_engine = RiskEngine(self.settings)
        self.order_builder = OrderBuilder(self.settings)

    def run_once(self, cycle_index: int = 1) -> PaperCycleResult:
        state = self.load_state()
        generated = 0
        opened = 0
        closed = 0
        rejected = 0
        errors: list[str] = []

        for symbol in self.symbols:
            try:
                snapshot = self._load_snapshot(symbol)
                state, close_count = _mark_and_close_positions(state, snapshot.option_contracts)
                closed += close_count

                regime_label = _regime_label_for_snapshot(snapshot)
                candidates = self.selector.generate_candidates(
                    contracts=snapshot.option_contracts,
                    underlying=snapshot.symbol,
                    dte=snapshot.dte,
                    score_inputs=_score_inputs_for_snapshot(
                        regime_label,
                        snapshot.option_contracts,
                    ),
                )
                generated += len(candidates)
                portfolio_state = _portfolio_state_from_paper(state)

                for candidate in candidates[: self.max_candidates_per_symbol]:
                    if self.strict_spec:
                        spec_decision = validate_candidate_against_strategy_spec(
                            candidate,
                            regime_label=regime_label,
                            account_equity=state.equity,
                        )
                        if not spec_decision.approved:
                            rejected += 1
                            self._record(
                                {
                                    "event_type": "paper_candidate_spec_rejected",
                                    "strict_spec": True,
                                    "symbol": symbol,
                                    "regime_label": regime_label.value,
                                    "candidate": candidate,
                                    "spec_reason_codes": spec_decision.reason_codes,
                                    "spec_warnings": spec_decision.warnings,
                                }
                            )
                            continue
                        if spec_decision.warnings:
                            self._record(
                                {
                                    "event_type": "paper_candidate_spec_warning",
                                    "strict_spec": True,
                                    "symbol": symbol,
                                    "regime_label": regime_label.value,
                                    "candidate": candidate,
                                    "spec_warnings": spec_decision.warnings,
                                }
                            )

                    decision = self.risk_engine.evaluate(candidate, portfolio_state)
                    if not decision.approved:
                        rejected += 1
                        self._record(
                            {
                                "event_type": "paper_candidate_rejected",
                                "symbol": symbol,
                                "candidate": candidate,
                                "risk_decision": decision,
                            }
                        )
                        continue

                    order = self.order_builder.build(candidate)
                    position = _paper_position_from_candidate(candidate, order.price_effect)
                    state = _add_open_position(state, position)
                    portfolio_state = _portfolio_state_from_paper(state)
                    opened += 1
                    self._record(
                        {
                            "event_type": "paper_position_opened",
                            "symbol": symbol,
                            "candidate": candidate,
                            "risk_decision": decision,
                            "paper_position": position,
                        }
                    )
            except Exception as exc:  # noqa: BLE001 - unattended loop must keep moving.
                errors.append(f"{symbol}: {exc.__class__.__name__}: {exc}")

        state = _replace_state(state, updated_at=datetime.now(UTC).isoformat())
        self.save_state(state)
        result = PaperCycleResult(
            cycle_index=cycle_index,
            source=self.source,
            symbols=self.symbols,
            generated_candidates=generated,
            opened_positions=opened,
            closed_positions=closed,
            rejected_candidates=rejected,
            errors=tuple(errors),
            state_path=str(self.state_path),
            summary=state.to_summary(),
            strict_spec=self.strict_spec,
        )
        self._record({"event_type": "paper_cycle", "result": result.to_dict()})
        return result

    def run(
        self,
        *,
        cycles: int,
        interval_seconds: float,
        days: float | None = None,
    ) -> list[PaperCycleResult]:
        if cycles < 0:
            raise ValueError("cycles must be zero or positive.")
        if interval_seconds < 0:
            raise ValueError("interval_seconds must be zero or positive.")
        if days is not None and days <= 0:
            raise ValueError("days must be positive.")

        deadline = datetime.now(UTC) + timedelta(days=days) if days is not None else None
        results: list[PaperCycleResult] = []
        cycle_index = 1
        while cycles == 0 or cycle_index <= cycles:
            results.append(self.run_once(cycle_index=cycle_index))
            if cycles != 0 and cycle_index >= cycles:
                break
            if deadline is not None and datetime.now(UTC) >= deadline:
                break
            cycle_index += 1
            time.sleep(interval_seconds)
        return results

    def load_state(self) -> PaperAccountState:
        if not self.state_path.exists():
            return PaperAccountState(starting_equity=self.starting_equity)
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        return _state_from_dict(payload)

    def save_state(self, state: PaperAccountState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(asdict(state), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _load_snapshot(self, symbol: str):
        if self.source == "mock":
            return DryRunBotRunner(
                settings=self.settings,
                source="mock",
                symbol=symbol,
                target_dte=self.target_dte,
            )._load_snapshot()
        data_source = self.tastytrade_data_source or TastytradeSdkDataSource.from_env(
            quote_timeout_seconds=self.quote_timeout_seconds,
            max_contracts=self.max_contracts,
        )
        return data_source.fetch_snapshot(symbol, self.target_dte)

    def _record(self, event: dict[str, Any]) -> None:
        self.audit_logger.record(event)


def _paper_position_from_candidate(
    candidate: StrategyCandidate,
    price_effect: str,
) -> PaperPosition:
    if candidate.max_loss is None:
        raise ValueError("Cannot paper trade a candidate without max_loss.")
    legs = tuple(_paper_leg_from_option_leg(leg) for leg in candidate.legs)
    exit_plan = asdict(candidate.exit_plan) if candidate.exit_plan is not None else {}
    return PaperPosition(
        position_id=uuid4().hex,
        opened_at=datetime.now(UTC).isoformat(),
        underlying=candidate.underlying,
        strategy_name=candidate.strategy_name,
        dte_at_entry=candidate.dte,
        entry_score=candidate.entry_score,
        max_profit=candidate.max_profit,
        max_loss=candidate.max_loss,
        expected_credit_or_debit=candidate.expected_credit_or_debit,
        price_effect=price_effect,
        entry_value=_position_value_from_legs(legs),
        legs=legs,
        exit_plan=exit_plan,
    )


def _paper_leg_from_option_leg(leg) -> PaperLeg:
    mid = leg.contract.effective_mid()
    if mid is None:
        raise ValueError(f"Cannot paper trade leg without mid price: {leg.contract.symbol}")
    return PaperLeg(
        symbol=leg.contract.symbol,
        action=leg.action.value,
        quantity=leg.quantity,
        option_type=leg.contract.option_type.value,
        strike=leg.contract.strike,
        expiration=leg.contract.expiration.isoformat(),
        entry_mid=round(mid, 4),
    )


def _position_value_from_legs(legs: tuple[PaperLeg, ...]) -> float:
    value = 0.0
    for leg in legs:
        signed = leg.entry_mid * 100 * leg.quantity
        value += signed if leg.action == OptionAction.BUY.value else -signed
    return round(value, 2)


def _mark_and_close_positions(
    state: PaperAccountState,
    contracts: tuple[OptionContract, ...],
) -> tuple[PaperAccountState, int]:
    contract_map = {contract.symbol: contract for contract in contracts}
    open_positions: list[PaperPosition] = []
    closed_trades = list(state.closed_trades)
    realized_pnl = state.realized_pnl
    closed_count = 0

    for position in state.open_positions:
        marked = _mark_position(position, contract_map)
        exit_reason = _exit_reason(marked)
        if exit_reason is None:
            open_positions.append(marked)
            continue
        realized_pnl = round(realized_pnl + marked.unrealized_pnl, 2)
        closed_trades.append(
            PaperClosedTrade(
                position=marked,
                closed_at=datetime.now(UTC).isoformat(),
                exit_reason=exit_reason,
                realized_pnl=marked.unrealized_pnl,
            )
        )
        closed_count += 1

    return (
        PaperAccountState(
            starting_equity=state.starting_equity,
            realized_pnl=realized_pnl,
            open_positions=tuple(open_positions),
            closed_trades=tuple(closed_trades),
            created_at=state.created_at,
            updated_at=datetime.now(UTC).isoformat(),
        ),
        closed_count,
    )


def _mark_position(
    position: PaperPosition,
    contract_map: dict[str, OptionContract],
) -> PaperPosition:
    current_value = 0.0
    all_marked = True
    for leg in position.legs:
        contract = contract_map.get(leg.symbol)
        mid = contract.effective_mid() if contract is not None else None
        if mid is None:
            all_marked = False
            mid = leg.entry_mid
        signed = mid * 100 * leg.quantity
        current_value += signed if leg.action == OptionAction.BUY.value else -signed

    pnl = round(current_value - position.entry_value, 2)
    if not all_marked:
        pnl = position.unrealized_pnl
        current_value = position.last_mark_value or position.entry_value
    return _replace_position(
        position,
        last_mark_value=round(current_value, 2),
        unrealized_pnl=pnl,
        last_marked_at=datetime.now(UTC).isoformat(),
    )


def _exit_reason(position: PaperPosition) -> str | None:
    plan = position.exit_plan
    pnl = position.unrealized_pnl
    max_profit = position.max_profit or 0.0
    max_loss = position.max_loss

    profit_target_pct = plan.get("profit_target_pct")
    if profit_target_pct is not None and max_profit > 0 and pnl >= max_profit * profit_target_pct:
        return "profit_target"

    stop_loss_pct = plan.get("stop_loss_pct")
    if stop_loss_pct is not None and pnl <= -(max_loss * stop_loss_pct):
        return "stop_loss"

    stop_loss_multiple = plan.get("stop_loss_multiple")
    if stop_loss_multiple is not None and pnl <= -(
        abs(position.expected_credit_or_debit) * stop_loss_multiple
    ):
        return "stop_loss_multiple"

    if _days_to_earliest_expiration(position) <= 0:
        return "expiration"

    return None


def _days_to_earliest_expiration(position: PaperPosition) -> int:
    today = datetime.now(UTC).date()
    expirations = [datetime.fromisoformat(leg.expiration).date() for leg in position.legs]
    return min((expiration - today).days for expiration in expirations)


def _portfolio_state_from_paper(state: PaperAccountState) -> PortfolioState:
    today = datetime.now(UTC).date()
    week_start = today - timedelta(days=today.weekday())
    today_opens = 0
    week_opens = 0
    for position in state.open_positions:
        opened_date = datetime.fromisoformat(position.opened_at).date()
        if opened_date == today:
            today_opens += 1
        if opened_date >= week_start:
            week_opens += 1
    return PortfolioState(
        account_equity=state.equity,
        open_positions=tuple(
            OpenPosition(
                symbol=position.underlying,
                strategy_name=position.strategy_name,
                max_loss=position.max_loss,
            )
            for position in state.open_positions
        ),
        daily_realized_pnl=_realized_pnl_since(state, today),
        weekly_realized_pnl=_realized_pnl_since(state, week_start),
        new_trades_today=today_opens,
        new_trades_this_week=week_opens,
    )


def _realized_pnl_since(state: PaperAccountState, start_date) -> float:
    total = 0.0
    for trade in state.closed_trades:
        closed_date = datetime.fromisoformat(trade.closed_at).date()
        if closed_date >= start_date:
            total += trade.realized_pnl
    return round(total, 2)


def _add_open_position(
    state: PaperAccountState,
    position: PaperPosition,
) -> PaperAccountState:
    return PaperAccountState(
        starting_equity=state.starting_equity,
        realized_pnl=state.realized_pnl,
        open_positions=(*state.open_positions, position),
        closed_trades=state.closed_trades,
        created_at=state.created_at,
        updated_at=datetime.now(UTC).isoformat(),
    )


def _replace_state(state: PaperAccountState, **changes: Any) -> PaperAccountState:
    values = asdict(state)
    values.update(changes)
    return _state_from_dict(values)


def _replace_position(position: PaperPosition, **changes: Any) -> PaperPosition:
    values = asdict(position)
    values.update(changes)
    return _position_from_dict(values)


def _state_from_dict(payload: dict[str, Any]) -> PaperAccountState:
    return PaperAccountState(
        starting_equity=float(payload.get("starting_equity", 2000.0)),
        realized_pnl=float(payload.get("realized_pnl", 0.0)),
        open_positions=tuple(
            _position_from_dict(item) for item in payload.get("open_positions", [])
        ),
        closed_trades=tuple(
            PaperClosedTrade(
                position=_position_from_dict(item["position"]),
                closed_at=item["closed_at"],
                exit_reason=item["exit_reason"],
                realized_pnl=float(item["realized_pnl"]),
            )
            for item in payload.get("closed_trades", [])
        ),
        created_at=payload.get("created_at") or datetime.now(UTC).isoformat(),
        updated_at=payload.get("updated_at") or datetime.now(UTC).isoformat(),
    )


def _position_from_dict(payload: dict[str, Any]) -> PaperPosition:
    return PaperPosition(
        position_id=payload["position_id"],
        opened_at=payload["opened_at"],
        underlying=payload["underlying"],
        strategy_name=payload["strategy_name"],
        dte_at_entry=int(payload["dte_at_entry"]),
        entry_score=float(payload["entry_score"]),
        max_profit=payload.get("max_profit"),
        max_loss=float(payload["max_loss"]),
        expected_credit_or_debit=float(payload["expected_credit_or_debit"]),
        price_effect=payload["price_effect"],
        entry_value=float(payload["entry_value"]),
        legs=tuple(PaperLeg(**item) for item in payload.get("legs", [])),
        exit_plan=dict(payload.get("exit_plan", {})),
        last_mark_value=payload.get("last_mark_value"),
        unrealized_pnl=float(payload.get("unrealized_pnl", 0.0)),
        last_marked_at=payload.get("last_marked_at"),
    )
