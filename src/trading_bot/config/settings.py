from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Literal, TypeVar

ExecutionMode = Literal["research", "backtest", "paper", "dry_run"]

ALLOWED_EXECUTION_MODES: frozenset[str] = frozenset({"research", "backtest", "paper", "dry_run"})
DEFAULT_CONFIG_PATH = Path(__file__).with_name("risk_limits.yaml")


@dataclass(frozen=True)
class AccountConfig:
    assumed_equity: float = 2000.0


@dataclass(frozen=True)
class RiskConfig:
    default_mode: ExecutionMode = "dry_run"
    per_trade_max_loss_pct_default: float = 0.20
    per_trade_max_loss_pct_high_score: float = 0.40
    soxl_per_trade_max_loss: float = 150.0
    total_open_max_loss_pct: float = 0.50
    daily_loss_limit: float = 200.0
    weekly_loss_limit: float = 400.0
    max_consecutive_losses: int = 3
    max_new_trades_per_day: int = 1
    max_new_trades_per_week: int = 5
    max_same_symbol_open_positions: int = 2
    max_same_strategy_open_positions: int = 3
    min_account_cash_buffer_pct: float = 0.25
    drawdown_stop_tightening_enabled: bool = True
    drawdown_stop_tightening_threshold_pct: float = 0.10
    drawdown_stop_tightening_multiplier: float = 0.85
    paper_capital_preservation_enabled: bool = True
    paper_capital_gate_per_trade_max_loss_abs: float = 100.0
    paper_capital_gate_per_trade_max_loss_pct: float = 0.05
    paper_capital_gate_total_open_max_loss_pct: float = 0.12
    paper_capital_gate_max_same_symbol_open_positions: int = 1
    paper_capital_gate_max_same_symbol_same_direction_positions: int = 1
    paper_capital_gate_drawdown_max_new_trades_per_day: int = 1

    def __post_init__(self) -> None:
        if self.default_mode not in ALLOWED_EXECUTION_MODES:
            allowed = ", ".join(sorted(ALLOWED_EXECUTION_MODES))
            raise ValueError(
                f"Unsupported execution mode {self.default_mode!r}. "
                f"Early versions allow only: {allowed}."
            )

    def per_trade_max_loss_cap(self, risk_budget_base: float, entry_score: float) -> float:
        pct_cap = (
            self.per_trade_max_loss_pct_high_score
            if entry_score >= 80
            else self.per_trade_max_loss_pct_default
        )
        return risk_budget_base * pct_cap


@dataclass(frozen=True)
class ForbiddenConfig:
    allow_0dte: bool = False
    allow_naked_options: bool = False
    allow_market_orders_options: bool = False
    allow_live_trading_default: bool = False


@dataclass(frozen=True)
class LiquidityConfig:
    max_bid_ask_pct_of_mid: float = 0.15
    max_abs_bid_ask_width: float = 0.10
    max_package_bid_ask_pct_of_entry: float = 0.15
    min_open_interest: int = 100
    min_volume: int = 10
    allow_missing_greeks: bool = False
    paper_liquidity_observation_mode: bool = False
    observation_max_contracts: int = 1


@dataclass(frozen=True)
class DteConfig:
    short_premium_min: int = 21
    short_premium_max: int = 60
    neutral_range_min: int = 30
    neutral_range_max: int = 45
    trend_qqq_nvda_min: int = 14
    trend_qqq_nvda_max: int = 45
    trend_soxl_min: int = 7
    trend_soxl_max: int = 21
    calendar_front_min: int = 7
    calendar_front_max: int = 21
    calendar_back_min: int = 30
    calendar_back_max: int = 60
    forbidden_dte_min: int = 1


@dataclass(frozen=True)
class DeltaConfig:
    short_premium_min_abs: float = 0.16
    short_premium_max_abs: float = 0.25
    iron_condor_short_min_abs: float = 0.16
    iron_condor_short_max_abs: float = 0.25
    trend_long_min_abs: float = 0.45
    trend_long_max_abs: float = 0.65
    trend_short_min_abs: float = 0.20
    trend_short_max_abs: float = 0.40


@dataclass(frozen=True)
class ExecutionConfig:
    credit_spread_limit_offset_from_mid: float = 0.02
    debit_spread_limit_offset_from_mid: float = 0.02
    max_price_chase_attempts: int = 2
    price_chase_increment: float = 0.01


@dataclass(frozen=True)
class StrategyConfig:
    min_entry_score: float = 55.0
    good_entry_score: float = 65.0
    high_quality_score: float = 80.0
    credit_spread_min_pct_of_width: float = 0.18
    credit_spread_max_pct_of_width: float = 0.35
    credit_spread_profit_target: float = 0.50
    iron_condor_profit_target: float = 0.35
    debit_spread_profit_target: float = 0.75
    debit_spread_profit_target_pct_of_debit: float = 0.60
    calendar_profit_target: float = 0.25
    credit_spread_stop_multiple: float = 2.0
    debit_spread_stop_loss: float = 0.45
    calendar_stop_loss: float = 0.35
    debit_spread_opening_cooldown_minutes: int = 20
    debit_spread_require_price_action_confirmation: bool = True
    debit_spread_anti_chase_atr_multiple: float = 1.0
    debit_spread_anti_chase_hard_atr_multiple: float = 1.5
    debit_spread_anti_chase_candle_count: int = 3
    debit_spread_strong_candle_body_pct: float = 0.60
    debit_spread_strong_candle_min_body_atr_multiple: float = 0.50
    debit_spread_pa_lookback_candles: int = 5
    debit_spread_pa_min_body_atr_multiple: float = 0.25
    debit_spread_pa_vwap_reclaim_tolerance_atr_multiple: float = 0.20
    qqq_put_credit_spread_quality_enabled: bool = True
    qqq_put_credit_spread_preferred_delta_max: float = 0.20
    qqq_put_credit_spread_min_atr_cushion: float = 1.0
    qqq_put_credit_spread_strong_atr_cushion: float = 1.5
    qqq_put_credit_spread_preferred_width: float = 2.0
    qqq_put_credit_spread_preferred_credit_pct_max: float = 0.30
    nvda_debit_spread_experimental_enabled: bool = False
    nvda_debit_spread_min_entry_score: float = 80.0
    nvda_debit_spread_max_iv_rank: float = 45.0
    nvda_debit_spread_min_reward_risk: float = 1.5
    nvda_debit_spread_max_width: float = 5.0
    debit_spread_min_reward_risk: float = 1.35
    credit_spread_min_planned_reward_risk: float = 0.25
    credit_spread_high_quality_override_score: float = 80.0
    exit_plan_quality_enabled: bool = True
    exit_plan_quality_monitor_only: bool = True
    credit_spread_max_planned_loss_pct_of_max_loss: float = 0.60
    credit_spread_hard_stop_pct_of_max_loss: float = 0.55
    credit_spread_eod_stop_tighten_minutes: int = 20
    credit_spread_eod_stop_tighten_pct: float = 0.80
    debit_spread_warning_loss_pct: float = 0.35
    debit_spread_hard_stop_pct_of_max_loss: float = 0.45
    debit_spread_invalidated_stop_requires_two_snapshots: bool = True
    debit_spread_min_planned_reward_risk: float = 1.25
    debit_spread_max_planned_loss_pct_of_max_loss: float = 0.45


@dataclass(frozen=True)
class SizingConfig:
    low_score_target_risk_pct: float = 0.01
    good_score_target_risk_pct: float = 0.025
    high_score_target_risk_pct: float = 0.04
    low_score_max_contracts: int = 1
    good_score_max_contracts: int = 2
    high_score_max_contracts: int = 3
    same_symbol_multiplier: float = 0.50
    same_strategy_multiplier: float = 0.75
    crowded_portfolio_threshold_pct: float = 0.25
    crowded_portfolio_multiplier: float = 0.75
    preservation_enabled: bool = True
    preservation_drawdown_threshold_pct: float = 0.10
    preservation_per_trade_max_loss_pct: float = 0.05
    preservation_per_trade_max_loss_abs: float = 100.0
    preservation_total_open_max_loss_pct: float = 0.12
    preservation_max_contracts: int = 1
    preservation_disable_scale_up: bool = True
    scale_up_enabled: bool = False
    scale_up_min_closed_trades: int = 20
    scale_up_min_profit_factor: float = 1.20
    scale_up_max_drawdown_pct: float = 0.08
    scale_up_min_positive_expectancy_per_trade: float = 0.01
    scale_up_increment_contracts: int = 1
    scale_up_max_contracts_absolute: int = 3


@dataclass(frozen=True)
class AllocationConfig:
    enabled: bool = True
    preservation_only: bool = True
    qqq_preservation_max_open_risk_pct: float = 0.10
    nvda_preservation_max_open_risk_pct: float = 0.08
    soxl_preservation_max_open_risk_pct: float = 0.05
    tech_beta_cluster_symbols: str = "QQQ,NVDA,SOXL"
    tech_beta_cluster_max_open_risk_pct: float = 0.20
    experimental_budget_pct: float = 0.03
    max_active_experiments: int = 1
    experimental_only_symbols: str = "SOXL"


@dataclass(frozen=True)
class SelectionConfig:
    enabled: bool = True
    normal_min_opportunity_score: float = 70.0
    preservation_min_opportunity_score: float = 78.0
    min_top_score_gap: float = 8.0
    lower_max_loss_tie_breaker_pct: float = 0.75


@dataclass(frozen=True)
class DuplicateCorrelationConfig:
    enabled: bool = True
    exact_duplicate_reject: bool = True
    near_duplicate_reject: bool = True
    near_duplicate_expiry_days: int = 7
    near_duplicate_min_strike_overlap_pct: float = 0.50
    near_duplicate_max_loss_similarity_pct: float = 0.25
    max_positions_per_symbol_direction: int = 1
    max_positions_per_thesis_bucket: int = 1
    max_bullish_tech_beta_positions: int = 1
    max_bearish_tech_beta_positions: int = 1
    stopout_cooldown_days_same_symbol_strategy: int = 1
    stopout_cooldown_days_same_thesis_after_two_stopouts: int = 2
    preservation_block_open_max_loss_pct: float = 0.20


@dataclass(frozen=True)
class BotSettings:
    account: AccountConfig
    risk: RiskConfig
    forbidden: ForbiddenConfig
    liquidity: LiquidityConfig
    dte: DteConfig
    delta: DeltaConfig
    execution: ExecutionConfig
    strategy: StrategyConfig
    sizing: SizingConfig
    allocation: AllocationConfig
    selection: SelectionConfig
    duplicate_correlation: DuplicateCorrelationConfig


T = TypeVar("T")


def load_settings(
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> BotSettings:
    """Load bot settings from the checked-in risk config plus environment overrides."""

    values = _load_config_values(Path(config_path) if config_path else DEFAULT_CONFIG_PATH)
    env_values = os.environ if env is None else env

    account_values = dict(values.get("account", {}))
    risk_values = dict(values.get("risk", {}))
    forbidden_values = dict(values.get("forbidden", {}))

    if "ASSUMED_EQUITY" in env_values:
        account_values["assumed_equity"] = float(env_values["ASSUMED_EQUITY"])

    if "TRADING_BOT_MODE" in env_values:
        risk_values["default_mode"] = env_values["TRADING_BOT_MODE"]

    # ENABLE_LIVE_TRADING is intentionally not enough to enable live behavior. It is kept
    # visible here only so config consumers can audit that live defaults remain disabled.
    if _parse_env_bool(env_values.get("ENABLE_LIVE_TRADING", "false")):
        forbidden_values["allow_live_trading_default"] = False

    return BotSettings(
        account=_build_dataclass(AccountConfig, account_values),
        risk=_build_dataclass(RiskConfig, risk_values),
        forbidden=_build_dataclass(ForbiddenConfig, forbidden_values),
        liquidity=_build_dataclass(LiquidityConfig, values.get("liquidity", {})),
        dte=_build_dataclass(DteConfig, values.get("dte", {})),
        delta=_build_dataclass(DeltaConfig, values.get("delta", {})),
        execution=_build_dataclass(ExecutionConfig, values.get("execution", {})),
        strategy=_build_dataclass(StrategyConfig, values.get("strategy", {})),
        sizing=_build_dataclass(SizingConfig, values.get("sizing", {})),
        allocation=_build_dataclass(AllocationConfig, values.get("allocation", {})),
        selection=_build_dataclass(SelectionConfig, values.get("selection", {})),
        duplicate_correlation=_build_dataclass(
            DuplicateCorrelationConfig,
            values.get("duplicate_correlation", {}),
        ),
    )


def _load_config_values(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return _parse_simple_yaml(path.read_text(encoding="utf-8"))


def _build_dataclass(cls: type[T], values: Mapping[str, Any]) -> T:
    field_names = {field.name for field in fields(cls)}
    kwargs = {key: value for key, value in values.items() if key in field_names}
    return cls(**kwargs)


def _parse_simple_yaml(text: str) -> dict[str, dict[str, Any]]:
    """Parse the small two-level YAML config format used by risk_limits.yaml."""

    parsed: dict[str, dict[str, Any]] = {}
    current_section: str | None = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line_without_comment = raw_line.split("#", 1)[0].rstrip()
        if not line_without_comment.strip():
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = line_without_comment.strip()

        if indent == 0 and stripped.endswith(":"):
            current_section = stripped[:-1]
            parsed[current_section] = {}
            continue

        if indent == 2 and current_section is not None and ":" in stripped:
            key, raw_value = stripped.split(":", 1)
            parsed[current_section][key.strip()] = _parse_scalar(raw_value.strip())
            continue

        raise ValueError(f"Unsupported config syntax on line {line_number}: {raw_line!r}")

    return parsed


def _parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value == "":
        return ""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_env_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
