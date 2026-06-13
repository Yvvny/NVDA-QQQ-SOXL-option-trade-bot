from trading_bot.config.settings import load_settings
from trading_bot.risk.budget import build_risk_budget_snapshot
from trading_bot.risk.portfolio import OpenPosition, PortfolioState


def test_risk_budget_snapshot_subtracts_existing_open_max_loss_from_available_cash_cap():
    settings = load_settings(env={})
    portfolio = PortfolioState(
        account_equity=1705.5,
        risk_budget_base=1198.0,
        open_positions=(
            OpenPosition("NVDA", "call_debit_spread", 327.5),
            OpenPosition("NVDA", "call_debit_spread", 180.0),
        ),
    )

    snapshot = build_risk_budget_snapshot(
        settings=settings,
        portfolio_state=portfolio,
        entry_score=66,
    )

    assert snapshot.available_cash == 1198.0
    assert snapshot.existing_open_max_loss == 507.5
    assert snapshot.configured_total_open_max_loss_cap == 599.0
    assert snapshot.remaining_total_risk_budget == 91.5
    assert snapshot.required_cash_reserve == 299.5
    assert snapshot.effective_new_trade_max_loss_capacity == 91.5
