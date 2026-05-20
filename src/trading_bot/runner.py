from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from trading_bot.config.settings import BotSettings, load_settings
from trading_bot.core.enums import OptionType
from trading_bot.core.models import OptionContract
from trading_bot.data.tastytrade_source import TastytradeMarketSnapshot, TastytradeSdkDataSource
from trading_bot.execution.dry_run import DryRunExecutionResult, DryRunExecutor
from trading_bot.regime.classifier import MarketRegimeInput, RegimeClassifier, RegimeLabel
from trading_bot.risk.portfolio import OpenPosition, PortfolioState
from trading_bot.storage.audit import JsonlAuditLogger
from trading_bot.strategies.diagnostics import build_scan_diagnostics
from trading_bot.strategies.scoring import StrategyScoreInput
from trading_bot.strategies.selector import StrategySelector


@dataclass(frozen=True)
class BotRunCycleResult:
    cycle_index: int
    source: str
    mode: str
    regime_label: str
    generated_candidates: int
    attempted_candidates: int
    accepted: int
    rejected: int
    audit_log_path: str
    statuses: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class DryRunBotRunner:
    def __init__(
        self,
        settings: BotSettings | None = None,
        source: str = "mock",
        audit_log_path: str | Path = "docs/reports/trade_audit.jsonl",
        max_candidates_per_cycle: int = 1,
        symbol: str = "QQQ",
        target_dte: int = 30,
        quote_timeout_seconds: float = 8.0,
        max_contracts: int = 120,
        tastytrade_data_source: TastytradeSdkDataSource | None = None,
    ) -> None:
        if source not in {"mock", "tastytrade"}:
            raise ValueError("source must be 'mock' or 'tastytrade'.")
        if max_candidates_per_cycle <= 0:
            raise ValueError("max_candidates_per_cycle must be positive.")
        if target_dte <= 0:
            raise ValueError("target_dte must be positive.")

        self.settings = settings or load_settings()
        self.source = source
        self.audit_log_path = Path(audit_log_path)
        self.max_candidates_per_cycle = max_candidates_per_cycle
        self.symbol = symbol.upper()
        self.target_dte = target_dte
        self.quote_timeout_seconds = quote_timeout_seconds
        self.max_contracts = max_contracts
        self.tastytrade_data_source = tastytrade_data_source
        self.selector = StrategySelector(self.settings)
        self.executor = DryRunExecutor(
            audit_logger=JsonlAuditLogger(self.audit_log_path),
        )

    def run_once(self, cycle_index: int = 1) -> BotRunCycleResult:
        snapshot = self._load_snapshot()
        regime_label = _regime_label_for_snapshot(snapshot)
        contracts = snapshot.option_contracts
        score_inputs = _score_inputs_for_snapshot(regime_label, contracts)
        candidates = self.selector.generate_candidates(
            contracts=contracts,
            underlying=snapshot.symbol,
            dte=snapshot.dte,
            score_inputs=score_inputs,
        )
        self.executor.audit_logger.record(
            {
                "event_type": "scan_diagnostics",
                "cycle_index": cycle_index,
                "source": self.source,
                "diagnostics": build_scan_diagnostics(
                    settings=self.settings,
                    symbol=snapshot.symbol,
                    expiration=snapshot.expiration,
                    dte=snapshot.dte,
                    underlying_quote=snapshot.underlying_quote,
                    contracts=contracts,
                    regime_label=regime_label,
                    score_inputs=score_inputs,
                    candidates=candidates,
                    market_data_diagnostics=snapshot.market_data_diagnostics,
                ),
            }
        )
        portfolio_state = PortfolioState(account_equity=self.settings.account.assumed_equity)
        results: list[DryRunExecutionResult] = []

        for candidate in candidates[: self.max_candidates_per_cycle]:
            result = self.executor.execute(candidate, portfolio_state)
            results.append(result)
            if result.risk_decision.approved and candidate.max_loss is not None:
                portfolio_state = PortfolioState(
                    account_equity=portfolio_state.account_equity,
                    open_positions=(
                        *portfolio_state.open_positions,
                        OpenPosition(
                            symbol=candidate.underlying,
                            strategy_name=candidate.strategy_name,
                            max_loss=candidate.max_loss,
                        ),
                    ),
                    daily_realized_pnl=portfolio_state.daily_realized_pnl,
                    weekly_realized_pnl=portfolio_state.weekly_realized_pnl,
                    consecutive_losses=portfolio_state.consecutive_losses,
                    new_trades_today=portfolio_state.new_trades_today + 1,
                    new_trades_this_week=portfolio_state.new_trades_this_week + 1,
                    kill_switch=portfolio_state.kill_switch,
                )

        return BotRunCycleResult(
            cycle_index=cycle_index,
            source=self.source,
            mode=self.settings.risk.default_mode,
            regime_label=regime_label.value,
            generated_candidates=len(candidates),
            attempted_candidates=len(results),
            accepted=sum(1 for result in results if result.risk_decision.approved),
            rejected=sum(1 for result in results if not result.risk_decision.approved),
            audit_log_path=str(self.audit_log_path),
            statuses=tuple(result.status for result in results),
        )

    def _load_snapshot(self) -> TastytradeMarketSnapshot:
        if self.source == "mock":
            expiration = date(2026, 6, 19)
            return TastytradeMarketSnapshot(
                symbol="QQQ",
                expiration=expiration,
                dte=30,
                underlying_quote=None,
                option_contracts=_mock_option_chain(),
            )

        data_source = self.tastytrade_data_source or TastytradeSdkDataSource.from_env(
            quote_timeout_seconds=self.quote_timeout_seconds,
            max_contracts=self.max_contracts,
        )
        return data_source.fetch_snapshot(self.symbol, self.target_dte)

    def run(self, cycles: int, interval_seconds: float) -> list[BotRunCycleResult]:
        if cycles < 0:
            raise ValueError("cycles must be zero or positive.")
        if interval_seconds < 0:
            raise ValueError("interval_seconds must be zero or positive.")

        results: list[BotRunCycleResult] = []
        cycle_index = 1
        while cycles == 0 or cycle_index <= cycles:
            results.append(self.run_once(cycle_index=cycle_index))
            if cycles != 0 and cycle_index >= cycles:
                break
            cycle_index += 1
            time.sleep(interval_seconds)
        return results


def _regime_label_for_snapshot(snapshot: TastytradeMarketSnapshot) -> RegimeLabel:
    if snapshot.underlying_quote is None:
        if snapshot.symbol == "QQQ":
            return _fallback_regime_label()
        return RegimeLabel.RANGE_LOW_IV

    decision = RegimeClassifier().classify(
        MarketRegimeInput(
            qqq_close=snapshot.underlying_quote.last if snapshot.symbol == "QQQ" else None,
        )
    )
    return decision.label


def _fallback_regime_label() -> RegimeLabel:
    return RegimeLabel.BULL_TREND_HIGH_IV


def _score_inputs_for_snapshot(
    regime_label: RegimeLabel,
    contracts: tuple[OptionContract, ...],
) -> tuple[StrategyScoreInput, ...]:
    bid_ask_pct = _median_bid_ask_pct_of_mid(contracts)
    volume, open_interest = _activity_from_contracts(contracts)
    return (
        StrategyScoreInput(
            strategy_name="put_credit_spread",
            regime_label=regime_label,
            iv_rank=None,
            bid_ask_pct_of_mid=bid_ask_pct,
            volume=volume,
            open_interest=open_interest,
            price_above_ema20=None,
            price_above_vwap=None,
            breakout_or_pullback_confirmed=False,
        ),
        StrategyScoreInput(
            strategy_name="call_credit_spread",
            regime_label=regime_label,
            iv_rank=None,
            bid_ask_pct_of_mid=bid_ask_pct,
            volume=volume,
            open_interest=open_interest,
            price_above_ema20=None,
            price_above_vwap=None,
            breakout_or_pullback_confirmed=False,
        ),
        StrategyScoreInput(
            strategy_name="call_debit_spread",
            regime_label=regime_label,
            iv_rank=None,
            bid_ask_pct_of_mid=bid_ask_pct,
            volume=volume,
            open_interest=open_interest,
            price_above_ema20=None,
            price_above_vwap=None,
            breakout_or_pullback_confirmed=False,
        ),
        StrategyScoreInput(
            strategy_name="put_debit_spread",
            regime_label=regime_label,
            iv_rank=None,
            bid_ask_pct_of_mid=bid_ask_pct,
            volume=volume,
            open_interest=open_interest,
            price_above_ema20=None,
            price_above_vwap=None,
            breakout_or_pullback_confirmed=False,
        ),
    )


def _mock_score_inputs(regime_label: RegimeLabel) -> tuple[StrategyScoreInput, ...]:
    return (
        StrategyScoreInput(
            strategy_name="put_credit_spread",
            regime_label=regime_label,
            iv_rank=55,
            bid_ask_pct_of_mid=0.08,
            volume=100,
            open_interest=1000,
            price_above_ema20=True,
            price_above_vwap=True,
            breakout_or_pullback_confirmed=True,
        ),
        StrategyScoreInput(
            strategy_name="call_debit_spread",
            regime_label=regime_label,
            iv_rank=35,
            bid_ask_pct_of_mid=0.08,
            volume=100,
            open_interest=1000,
            price_above_ema20=True,
            price_above_vwap=True,
            breakout_or_pullback_confirmed=True,
        ),
    )


def _mock_option_chain() -> tuple[OptionContract, ...]:
    expiration = date(2026, 6, 19)
    return (
        _contract(expiration, "put", 450, -0.25, 0.49, 0.51),
        _contract(expiration, "put", 449, -0.10, 0.24, 0.26),
        _contract(expiration, "call", 510, 0.55, 0.79, 0.81),
        _contract(expiration, "call", 512, 0.30, 0.29, 0.31),
    )


def _median_bid_ask_pct_of_mid(contracts: tuple[OptionContract, ...]) -> float | None:
    values = []
    for contract in contracts:
        mid = contract.effective_mid()
        if mid is None or mid <= 0 or contract.bid is None or contract.ask is None:
            continue
        values.append((contract.ask - contract.bid) / mid)
    if not values:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _activity_from_contracts(
    contracts: tuple[OptionContract, ...],
) -> tuple[int | None, int | None]:
    volumes = [contract.volume for contract in contracts if contract.volume is not None]
    open_interests = [
        contract.open_interest for contract in contracts if contract.open_interest is not None
    ]
    volume = min(volumes) if volumes else None
    open_interest = min(open_interests) if open_interests else None
    return volume, open_interest


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
