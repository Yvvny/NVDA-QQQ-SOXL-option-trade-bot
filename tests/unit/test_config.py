import pytest

from trading_bot.config.settings import load_settings


def test_default_config_loads_dry_run_mode():
    settings = load_settings(env={})

    assert settings.risk.default_mode == "dry_run"
    assert settings.account.assumed_equity == 2000
    assert settings.risk.per_trade_max_loss_pct_default == 0.20
    assert settings.risk.per_trade_max_loss_pct_high_score == 0.40
    assert settings.risk.total_open_max_loss_pct == 0.50
    assert settings.risk.drawdown_stop_tightening_enabled is True
    assert settings.risk.drawdown_stop_tightening_threshold_pct == 0.10
    assert settings.risk.drawdown_stop_tightening_multiplier == 0.85
    assert settings.risk.paper_capital_preservation_enabled is True
    assert settings.risk.paper_capital_gate_per_trade_max_loss_abs == 100
    assert settings.risk.paper_capital_gate_per_trade_max_loss_pct == 0.05
    assert settings.risk.max_new_trades_per_day == 1
    assert settings.risk.paper_capital_gate_total_open_max_loss_pct == 0.12
    assert settings.risk.paper_capital_gate_max_same_symbol_open_positions == 1
    assert settings.risk.paper_capital_gate_max_same_symbol_same_direction_positions == 1
    assert settings.risk.paper_capital_gate_drawdown_max_new_trades_per_day == 1
    assert settings.liquidity.max_abs_bid_ask_width == 0.10
    assert settings.liquidity.max_package_bid_ask_pct_of_entry == 0.15
    assert settings.liquidity.paper_liquidity_observation_mode is False
    assert settings.liquidity.observation_max_contracts == 1
    assert settings.sizing.low_score_target_risk_pct == 0.01
    assert settings.sizing.good_score_target_risk_pct == 0.025
    assert settings.sizing.high_score_target_risk_pct == 0.04
    assert settings.sizing.low_score_max_contracts == 1
    assert settings.sizing.good_score_max_contracts == 2
    assert settings.sizing.high_score_max_contracts == 3
    assert settings.sizing.preservation_enabled is True
    assert settings.sizing.preservation_drawdown_threshold_pct == 0.10
    assert settings.sizing.preservation_per_trade_max_loss_pct == 0.05
    assert settings.sizing.preservation_per_trade_max_loss_abs == 100
    assert settings.sizing.preservation_total_open_max_loss_pct == 0.12
    assert settings.sizing.preservation_max_contracts == 1
    assert settings.sizing.preservation_disable_scale_up is True
    assert settings.sizing.scale_up_enabled is False
    assert settings.sizing.scale_up_min_closed_trades == 20
    assert settings.sizing.scale_up_min_profit_factor == 1.20
    assert settings.sizing.scale_up_max_drawdown_pct == 0.08
    assert settings.sizing.scale_up_min_positive_expectancy_per_trade == 0.01
    assert settings.sizing.scale_up_increment_contracts == 1
    assert settings.sizing.scale_up_max_contracts_absolute == 3
    assert settings.allocation.enabled is True
    assert settings.allocation.preservation_only is True
    assert settings.allocation.qqq_preservation_max_open_risk_pct == 0.10
    assert settings.allocation.nvda_preservation_max_open_risk_pct == 0.08
    assert settings.allocation.soxl_preservation_max_open_risk_pct == 0.05
    assert settings.allocation.tech_beta_cluster_symbols == "QQQ,NVDA,SOXL"
    assert settings.allocation.tech_beta_cluster_max_open_risk_pct == 0.20
    assert settings.allocation.experimental_budget_pct == 0.03
    assert settings.allocation.max_active_experiments == 1
    assert settings.allocation.experimental_only_symbols == "SOXL"
    assert settings.selection.enabled is True
    assert settings.selection.normal_min_opportunity_score == 70
    assert settings.selection.preservation_min_opportunity_score == 78
    assert settings.selection.min_top_score_gap == 8
    assert settings.selection.lower_max_loss_tie_breaker_pct == 0.75
    assert settings.duplicate_correlation.enabled is True
    assert settings.duplicate_correlation.near_duplicate_expiry_days == 7
    assert settings.duplicate_correlation.near_duplicate_min_strike_overlap_pct == 0.50
    assert settings.duplicate_correlation.near_duplicate_max_loss_similarity_pct == 0.25
    assert settings.duplicate_correlation.max_positions_per_symbol_direction == 1
    assert settings.duplicate_correlation.max_positions_per_thesis_bucket == 1
    assert settings.duplicate_correlation.max_bullish_tech_beta_positions == 1
    assert settings.duplicate_correlation.max_bearish_tech_beta_positions == 1
    assert settings.duplicate_correlation.stopout_cooldown_days_same_symbol_strategy == 1
    assert settings.duplicate_correlation.stopout_cooldown_days_same_thesis_after_two_stopouts == 2
    assert settings.duplicate_correlation.preservation_block_open_max_loss_pct == 0.20
    assert settings.strategy.debit_spread_opening_cooldown_minutes == 20
    assert settings.strategy.debit_spread_profit_target_pct_of_debit == 0.60
    assert settings.strategy.debit_spread_require_price_action_confirmation is True
    assert settings.strategy.debit_spread_anti_chase_atr_multiple == 1.0
    assert settings.strategy.debit_spread_anti_chase_hard_atr_multiple == 1.5
    assert settings.strategy.debit_spread_anti_chase_candle_count == 3
    assert settings.strategy.debit_spread_pa_lookback_candles == 5
    assert settings.strategy.debit_spread_pa_min_body_atr_multiple == 0.25
    assert settings.strategy.debit_spread_pa_vwap_reclaim_tolerance_atr_multiple == 0.20
    assert settings.strategy.qqq_put_credit_spread_quality_enabled is True
    assert settings.strategy.qqq_put_credit_spread_preferred_delta_max == 0.20
    assert settings.strategy.qqq_put_credit_spread_min_atr_cushion == 1.0
    assert settings.strategy.qqq_put_credit_spread_strong_atr_cushion == 1.5
    assert settings.strategy.qqq_put_credit_spread_preferred_width == 2.0
    assert settings.strategy.qqq_put_credit_spread_preferred_credit_pct_max == 0.30
    assert settings.strategy.nvda_debit_spread_experimental_enabled is False
    assert settings.strategy.nvda_debit_spread_min_entry_score == 80
    assert settings.strategy.nvda_debit_spread_max_iv_rank == 45
    assert settings.strategy.nvda_debit_spread_min_reward_risk == 1.5
    assert settings.strategy.nvda_debit_spread_max_width == 5.0
    assert settings.strategy.debit_spread_min_reward_risk == 1.35
    assert settings.strategy.credit_spread_min_planned_reward_risk == 0.25
    assert settings.strategy.credit_spread_high_quality_override_score == 80
    assert settings.strategy.exit_plan_quality_enabled is True
    assert settings.strategy.exit_plan_quality_monitor_only is True
    assert settings.strategy.credit_spread_max_planned_loss_pct_of_max_loss == 0.60
    assert settings.strategy.credit_spread_hard_stop_pct_of_max_loss == 0.55
    assert settings.strategy.credit_spread_eod_stop_tighten_minutes == 20
    assert settings.strategy.credit_spread_eod_stop_tighten_pct == 0.80
    assert settings.strategy.debit_spread_warning_loss_pct == 0.35
    assert settings.strategy.debit_spread_hard_stop_pct_of_max_loss == 0.45
    assert settings.strategy.debit_spread_invalidated_stop_requires_two_snapshots is True
    assert settings.strategy.debit_spread_min_planned_reward_risk == 1.25
    assert settings.strategy.debit_spread_max_planned_loss_pct_of_max_loss == 0.45
    assert settings.forbidden.allow_live_trading_default is False
    assert settings.forbidden.allow_0dte is False
    assert settings.forbidden.allow_naked_options is False
    assert settings.forbidden.allow_market_orders_options is False


def test_allowed_mode_can_be_overridden_by_environment():
    settings = load_settings(env={"TRADING_BOT_MODE": "paper"})

    assert settings.risk.default_mode == "paper"


def test_live_mode_is_rejected_in_early_versions():
    with pytest.raises(ValueError, match="Unsupported execution mode"):
        load_settings(env={"TRADING_BOT_MODE": "live"})


def test_enable_live_trading_env_does_not_enable_live_default():
    settings = load_settings(env={"ENABLE_LIVE_TRADING": "true"})

    assert settings.risk.default_mode == "dry_run"
    assert settings.forbidden.allow_live_trading_default is False
