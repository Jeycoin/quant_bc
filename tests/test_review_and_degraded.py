"""Tests for structured review extraction and memory degraded mode."""

import asyncio

import pytest

from agent.agent import extract_review_blocks
from agent.safety import SafetyGuard, SafetyViolation
from agent.agent import TradingAgent


def test_extract_review_blocks():
    text = (
        "复盘如下……\n```review\n"
        '{"execution_id": "abc", "outcome": "+0.18 USDT", '
        '"decision_quality": "good", "lesson": "grid works in range"}\n'
        "```\n更多文字\n```review\n"
        '{"execution_id": "def", "decision_quality": "bad"}\n```'
    )
    blocks = extract_review_blocks(text)
    assert len(blocks) == 2
    assert blocks[0]["decision_quality"] == "good"
    assert blocks[0]["lesson"] == "grid works in range"
    assert blocks[1]["execution_id"] == "def"


def test_extract_review_blocks_invalid_json_skipped():
    assert extract_review_blocks("```review\n{bad json}\n```") == []
    assert extract_review_blocks("no blocks") == []
    assert extract_review_blocks(None) == []


class _BrokenMemory:
    def record_decision(self, **kwargs):
        raise RuntimeError("disk full")

    def record_analysis(self, **kwargs):
        raise RuntimeError("disk full")

    def record_review(self, **kwargs):
        raise RuntimeError("disk full")


def _make_agent():
    guard = SafetyGuard(live_trading=False, config={
        "safety": {
            "paper_connector_patterns": ["testnet"],
            "allowed_quote_assets": ["USDT"],
            "allowed_base_assets": ["BTC"],
            "max_order_quote_amount": 500,
            "max_open_positions": 3,
            "blocked_tool_actions": {},
        },
        "memory": {"sqlite_path": "data/agent_memory.db"},
    })
    agent = TradingAgent.__new__(TradingAgent)
    agent.guard = guard
    agent.memory = _BrokenMemory()
    agent._memory_broken = False
    agent._created_executors = []
    agent._blocked = False
    agent._market_snapshot = {}
    return agent


def test_memory_failure_flips_degraded_mode():
    agent = _make_agent()
    agent._safe_record(agent.memory.record_decision, mode="PAPER",
                       market_context="", analysis="")
    assert agent._memory_broken is True


def test_degraded_mode_blocks_executor_creation():
    agent = _make_agent()
    agent._memory_broken = True
    with pytest.raises(SafetyViolation, match="Degraded mode"):
        asyncio.run(agent._check_position_capacity(
            "manage_executors", {"action": "create", "executor_config": {}}
        ))


def test_degraded_mode_still_allows_analysis_calls():
    agent = _make_agent()
    agent._memory_broken = True
    # non-executor tools pass through the capacity check untouched
    asyncio.run(agent._check_position_capacity("get_market_data", {}))
