from datetime import date

from trading_bot.core.enums import OptionAction, OptionType
from trading_bot.core.models import ExitPlan, OptionContract, OptionLeg, StrategyCandidate
from trading_bot.risk import OpenPosition, PortfolioState
from trading_bot.risk.sizing import PositionSizer


def test_position_sizer_scales_up_for_larger_available_cash():
    sizer = PositionSizer()

    small = sizer.size_candidate(_candidate(), PortfolioState(account_equity=2000))
    large = sizer.size_candidate(_candidate(), PortfolioState(account_equity=20000))

    assert small.quantity == 4
    assert large.quantity > small.quantity
    assert large.quantity == 20


def test_position_sizer_reduces_size_when_symbol_is_already_open():
    sizer = PositionSizer()
    portfolio = PortfolioState(
        account_equity=20000,
        open_positions=(OpenPosition("QQQ", "put_credit_spread", 100),),
    )

    sized = sizer.size_candidate(_candidate(), portfolio)

    assert sized.quantity < 20
    assert sized.quantity >= 1


def _candidate() -> StrategyCandidate:
    expiration = date(2026, 6, 19)
    return StrategyCandidate(
        strategy_name="put_credit_spread",
        underlying="QQQ",
        legs=(
            OptionLeg(
                contract=OptionContract(
                    symbol=f"QQQ {expiration.isoformat()} 450 put",
                    underlying="QQQ",
                    expiration=expiration,
                    strike=450,
                    option_type=OptionType.PUT,
                    bid=0.45,
                    ask=0.55,
                    mid=0.50,
                    delta=-0.25,
                    volume=100,
                    open_interest=1000,
                ),
                action=OptionAction.SELL,
            ),
            OptionLeg(
                contract=OptionContract(
                    symbol=f"QQQ {expiration.isoformat()} 449 put",
                    underlying="QQQ",
                    expiration=expiration,
                    strike=449,
                    option_type=OptionType.PUT,
                    bid=0.20,
                    ask=0.30,
                    mid=0.25,
                    delta=-0.10,
                    volume=100,
                    open_interest=1000,
                ),
                action=OptionAction.BUY,
            ),
        ),
        dte=30,
        entry_score=85,
        max_profit=50,
        max_loss=100,
        expected_credit_or_debit=50,
        reason_codes=("fixture",),
        exit_plan=ExitPlan(profit_target_pct=0.5, stop_loss_multiple=2.5),
    )
