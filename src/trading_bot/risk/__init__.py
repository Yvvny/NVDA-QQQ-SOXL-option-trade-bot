"""Portfolio risk controls and kill-switch logic."""

from trading_bot.risk.allocation import AllocationPosition, validate_symbol_allocation
from trading_bot.risk.budget import RiskBudgetSnapshot, build_risk_budget_snapshot
from trading_bot.risk.duplicate_correlation_gate import (
    GateLeg,
    GatePosition,
    evaluate_duplicate_correlation_gate,
)
from trading_bot.risk.engine import RiskEngine
from trading_bot.risk.kill_switch import KillSwitchState
from trading_bot.risk.liquidity import validate_candidate_liquidity
from trading_bot.risk.policy_audit import validate_pre_trade_invariants
from trading_bot.risk.portfolio import OpenPosition, PortfolioState
from trading_bot.risk.sizing import PositionSizer

__all__ = [
    "AllocationPosition",
    "GateLeg",
    "GatePosition",
    "KillSwitchState",
    "OpenPosition",
    "PortfolioState",
    "PositionSizer",
    "RiskBudgetSnapshot",
    "RiskEngine",
    "build_risk_budget_snapshot",
    "evaluate_duplicate_correlation_gate",
    "validate_candidate_liquidity",
    "validate_pre_trade_invariants",
    "validate_symbol_allocation",
]
