import pytest

from trading_bot.config.settings import load_settings


def test_default_config_loads_dry_run_mode():
    settings = load_settings(env={})

    assert settings.risk.default_mode == "dry_run"
    assert settings.account.assumed_equity == 2000
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
