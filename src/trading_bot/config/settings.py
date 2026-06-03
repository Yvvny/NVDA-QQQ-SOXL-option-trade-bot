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
    max_new_trades_per_day: int = 2
    max_new_trades_per_week: int = 5
    max_same_symbol_open_positions: int = 2
    max_same_strategy_open_positions: int = 3
    min_account_cash_buffer_pct: float = 0.25

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
    min_open_interest: int = 100
    min_volume: int = 10
    allow_missing_greeks: bool = False


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
    short_premium_min_abs: float = 0.10
    short_premium_max_abs: float = 0.35
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
    credit_spread_profit_target: float = 0.50
    iron_condor_profit_target: float = 0.35
    debit_spread_profit_target: float = 0.75
    calendar_profit_target: float = 0.25
    credit_spread_stop_multiple: float = 2.5
    debit_spread_stop_loss: float = 0.45
    calendar_stop_loss: float = 0.35


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
