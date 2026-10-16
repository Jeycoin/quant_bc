"""Unit tests for position-executor config normalization (v1.4.2).

The LLM kept emitting take_profit at top level and amount_quote instead of
base-unit amount; since 09-28 every directional proposal died at the cost
gate with 'could not determine trade notional'.
"""

import asyncio
from types import SimpleNamespace

from agent.agent import TradingAgent


def _make_agent():
    agent = TradingAgent.__new__(TradingAgent)
    agent.guard = SimpleNamespace(
        config={"risk": {"default_position_notional_quote": 500}})

    async def fake_price(cfg):
        return 80000.0

    agent._current_price = fake_price
    return agent


def test_barriers_moved_into_triple_barrier():
    agent = _make_agent()
    cfg = {"trading_pair": "BTC-USDT", "side": "SELL", "leverage": 20,
           "take_profit": 0.02, "stop_loss": 0.04, "time_limit": 21600,
           "amount_quote": 500}
    asyncio.run(agent._normalize_position_config(cfg))
    barrier = cfg["triple_barrier_config"]
    assert barrier["take_profit"] == 0.02
    assert barrier["stop_loss"] == 0.04
    assert barrier["time_limit"] == 21600
    assert "take_profit" not in cfg  # moved, not copied
    assert abs(cfg["amount"] - 500 / 80000.0) < 1e-12
    assert "amount_quote" not in cfg


def test_existing_barrier_not_overwritten():
    agent = _make_agent()
    cfg = {"trading_pair": "BTC-USDT", "amount": 0.01,
           "take_profit": 0.09,
           "triple_barrier_config": {"take_profit": 0.02}}
    asyncio.run(agent._normalize_position_config(cfg))
    assert cfg["triple_barrier_config"]["take_profit"] == 0.02
    assert cfg["amount"] == 0.01  # base amount wins, no quote conversion


def test_missing_size_uses_default_notional():
    agent = _make_agent()
    cfg = {"trading_pair": "BTC-USDT", "side": "SELL", "take_profit": 0.02}
    asyncio.run(agent._normalize_position_config(cfg))
    assert abs(cfg["amount"] - 500 / 80000.0) < 1e-12


def test_position_notional_accepts_quote_fields():
    agent = _make_agent()
    assert agent._position_notional({"amount": 0.01}, 80000) == 800.0
    assert agent._position_notional({"amount_quote": 500}, None) == 500.0
    assert agent._position_notional({"total_amount_quote": 300}, 80000) == 300.0
    assert agent._position_notional({}, 80000) is None
