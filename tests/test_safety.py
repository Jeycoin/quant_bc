"""SafetyGuard boundary tests — the core protection of this project."""

import os

import pytest

from agent.safety import SafetyGuard, SafetyViolation


@pytest.fixture()
def paper_guard(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "false")
    return SafetyGuard.from_env("config/settings.yaml")


def _paper_create(**overrides):
    cfg = {
        "connector_name": "binance_perpetual_testnet",
        "trading_pair": "BTC-USDT",
        "total_amount_quote": 100,
    }
    cfg.update(overrides)
    return {"action": "create", "executor_config": cfg}


def test_paper_mode_is_default(paper_guard):
    assert paper_guard.mode == "PAPER"


def test_testnet_executor_allowed(paper_guard):
    paper_guard.check_tool_call("manage_executors", _paper_create())


def test_live_connector_blocked_in_paper_mode(paper_guard):
    with pytest.raises(SafetyViolation, match="PAPER mode"):
        paper_guard.check_tool_call(
            "manage_executors", _paper_create(connector_name="binance_perpetual")
        )


def test_oversized_order_blocked(paper_guard):
    with pytest.raises(SafetyViolation, match="max_order_quote_amount"):
        paper_guard.check_tool_call(
            "manage_executors", _paper_create(total_amount_quote=5000)
        )


def test_non_whitelisted_base_blocked(paper_guard):
    with pytest.raises(SafetyViolation, match="Base asset"):
        paper_guard.check_tool_call(
            "manage_executors", _paper_create(trading_pair="DOGE-USDT")
        )


def test_non_whitelisted_quote_blocked(paper_guard):
    with pytest.raises(SafetyViolation, match="Quote asset"):
        paper_guard.check_tool_call(
            "manage_executors", _paper_create(trading_pair="BTC-EUR")
        )


def test_credential_delete_blocked(paper_guard):
    with pytest.raises(SafetyViolation, match="blocked"):
        paper_guard.check_tool_call(
            "setup_connector", {"action": "delete", "connector": "binance"}
        )


def test_read_only_tools_unrestricted(paper_guard):
    paper_guard.check_tool_call(
        "get_market_data",
        {"data_type": "candles", "connector_name": "binance", "trading_pair": "BTC-USDT"},
    )


def test_live_mode_allows_live_connector(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "true")
    guard = SafetyGuard.from_env("config/settings.yaml")
    assert guard.mode == "LIVE"
    guard.check_tool_call(
        "manage_executors", _paper_create(connector_name="binance_perpetual")
    )
