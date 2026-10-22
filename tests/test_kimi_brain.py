"""Unit tests for the manual-cockpit adapter (scripts/kimi_brain.py).

Covers decision-file parsing, schema validation, the pre-gate pipeline
(prepare/static_pipeline/min_confidence) and decision_event recording
(WAIT/WATCH, dry-run, gate rejection, executed). Network seams
(executor creation, gate evaluation, position capacity) are stubbed —
the real gate methods are covered by their own test suites.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from agent.memory.store import MemoryStore  # noqa: E402
from agent.safety import SafetyGuard  # noqa: E402
from analytics.store import AnalyticsStore  # noqa: E402
import kimi_brain  # noqa: E402
from kimi_brain import (  # noqa: E402
    KimiBrain,
    build_gate_agent,
    check_min_confidence,
    load_decisions,
    prepare_arguments,
    static_pipeline,
    validate_decision,
)

CONFIG = {
    "safety": {
        "paper_connector_patterns": ["_paper_trade", "testnet"],
        "allowed_quote_assets": ["USDT", "USDC", "USD"],
        "allowed_base_assets": ["BTC", "ETH", "SOL", "XRP", "SUI"],
        "max_order_quote_amount": 500,
        "max_open_positions": 3,
        "blocked_tool_actions": {},
    },
    "memory": {"sqlite_path": "data/agent_memory.db"},
    "risk": {"max_total_exposure_quote": 8000,
             "max_symbol_exposure_quote": 4000,
             "default_position_notional_quote": 500},
    "tunable": {
        "entry_gate.rsi_overbought": {"default": 75, "min": 65, "max": 85},
        "time_stop_hours": {"default": 6, "min": 2, "max": 24},
        "min_confidence": {"default": 0.6, "min": 0.5, "max": 0.85},
        "grid_take_profit_pct": {"default": 0.002, "min": 0.001, "max": 0.05},
        "position_notional_quote": {"default": 200, "min": 50, "max": 500},
    },
}


def _make_brain(tmp_path, validate_result=(None, None)):
    guard = SafetyGuard(live_trading=False, config=CONFIG)
    memory = MemoryStore(str(tmp_path / "mem.db"))
    analytics = AnalyticsStore(str(tmp_path / "analytics.db"))
    agent = build_gate_agent(guard, memory, analytics)

    async def fake_capacity(name, arguments):
        return None

    async def fake_validate(arguments, gate_config=None):
        return validate_result

    agent._check_position_capacity = fake_capacity
    agent._validate_executor_create = fake_validate
    return KimiBrain(guard, memory, analytics, agent)


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ file parsing


def test_load_decisions_single_object():
    assert load_decisions({"symbol": "BTC", "action": "WAIT"}) == [
        {"symbol": "BTC", "action": "WAIT"}]


def test_load_decisions_list_and_wrapped():
    two = [{"symbol": "BTC", "action": "WAIT"},
           {"symbol": "ETH", "action": "WATCH"}]
    assert load_decisions(two) == two
    assert load_decisions({"decisions": two}) == two


def test_load_decisions_rejects_garbage():
    with pytest.raises(ValueError):
        load_decisions("not a decision")
    with pytest.raises(ValueError):
        load_decisions([])
    with pytest.raises(ValueError):
        load_decisions([{"action": "WAIT"}, "oops"])


# ------------------------------------------------------------ schema check


def test_validate_wait_ok():
    assert validate_decision({"symbol": "BTC", "action": "WAIT",
                              "confidence": 0.5}) == []


def test_validate_action_and_symbol_required():
    errors = validate_decision({"action": "BUY"})
    assert any("action" in e for e in errors)
    assert any("symbol" in e for e in errors)
    errors = validate_decision({"symbol": "BTC", "action": "WAIT",
                                "confidence": 1.5})
    assert any("confidence" in e for e in errors)


def test_validate_propose_requires_config_and_thesis():
    errors = validate_decision({"symbol": "BTC", "action": "PROPOSE_GRID"})
    assert any("executor_config" in e for e in errors)
    assert any("thesis" in e for e in errors)
    errors = validate_decision({
        "symbol": "BTC", "action": "PROPOSE_POSITION",
        "thesis": "trend continuation",
        "executor_config": {"side": "LONG", "amount": 0.001}})
    assert any("invalidation" in e for e in errors)


def test_validate_accepts_string_executor_config():
    errors = validate_decision({
        "symbol": "BTC", "action": "PROPOSE_POSITION",
        "thesis": "t", "invalidation": "i",
        "executor_config": json.dumps({"side": "LONG", "amount": 0.001})})
    assert errors == []


# ------------------------------------------------------- argument building


def test_prepare_arguments_defaults():
    args = prepare_arguments(
        {"symbol": "SOL", "action": "PROPOSE_GRID",
         "executor_config": {"total_amount_quote": 100}}, CONFIG)
    cfg = args["executor_config"]
    assert args["action"] == "create"
    assert args["executor_type"] == "grid_executor"
    assert cfg["type"] == "grid_executor"
    assert cfg["connector_name"] == "binance_perpetual_testnet"
    assert cfg["trading_pair"] == "SOL-USDT"


def test_prepare_arguments_explicit_wins():
    args = prepare_arguments(
        {"symbol": "BTC", "action": "PROPOSE_POSITION",
         "executor_config": {"connector_name": "hyperliquid_perpetual_testnet",
                             "trading_pair": "BTC-USD", "side": "LONG"}},
        CONFIG)
    cfg = args["executor_config"]
    assert cfg["connector_name"] == "hyperliquid_perpetual_testnet"
    assert cfg["trading_pair"] == "BTC-USD"
    assert args["executor_type"] == "position_executor"


# ---------------------------------------------------------- static pipeline


def test_pipeline_grid_keep_position_and_clamps(tmp_path):
    brain = _make_brain(tmp_path)
    decision = {
        "symbol": "BTC", "action": "PROPOSE_GRID",
        "executor_config": {"total_amount_quote": 100,
                            "triple_barrier_config": {"take_profit": 0.10}},
        "gate_adjustments": {
            "entry_gate.rsi_overbought": {"value": 95, "reason": "strong trend"},
            "safety.max_order_quote_amount": {"value": 999, "reason": "x"},
        },
    }
    args = prepare_arguments(decision, CONFIG)
    report, gate_config = _run(static_pipeline(brain.agent, decision, args))
    cfg = args["executor_config"]
    assert cfg["keep_position"] is False  # v1.4.1 forced
    # grid take_profit clamped to the tunable ceiling 0.05
    assert cfg["triple_barrier_config"]["take_profit"] == 0.05
    # gate adjustment clamped 95 -> 85, unknown hard-limit path rejected
    assert report["overrides"]["entry_gate.rsi_overbought"] == 85
    assert any(e["path"] == "entry_gate.rsi_overbought" and e["clamped"]
               for e in report["events"])
    assert any(r["path"] == "safety.max_order_quote_amount"
               for r in report["rejected"])
    # effective config carries the override; the source config is untouched
    assert gate_config["entry_gate"]["rsi_overbought"] == 85
    assert CONFIG["tunable"]["entry_gate.rsi_overbought"]["default"] == 75


def test_pipeline_position_time_limit(tmp_path):
    brain = _make_brain(tmp_path)

    async def fake_price(cfg):
        return 80000.0

    brain.agent._current_price = fake_price
    base = {"symbol": "BTC", "action": "PROPOSE_POSITION",
            "executor_config": {"side": "LONG", "amount_quote": 200,
                                "take_profit": 0.02}}

    args = prepare_arguments(dict(base), CONFIG)
    _run(static_pipeline(brain.agent, dict(base), args))
    barrier = args["executor_config"]["triple_barrier_config"]
    assert barrier["time_limit"] == 6 * 3600  # default time_stop_hours

    adj = dict(base, gate_adjustments={
        "time_stop_hours": {"value": 12, "reason": "swing thesis"}})
    args = prepare_arguments(adj, CONFIG)
    _run(static_pipeline(brain.agent, adj, args))
    barrier = args["executor_config"]["triple_barrier_config"]
    assert barrier["time_limit"] == 12 * 3600  # override reaches the barrier


def test_pipeline_position_notional_normalized(tmp_path):
    brain = _make_brain(tmp_path)

    async def fake_price(cfg):
        return 80000.0

    brain.agent._current_price = fake_price
    decision = {"symbol": "BTC", "action": "PROPOSE_POSITION",
                "executor_config": {"side": "LONG", "amount_quote": 200,
                                    "take_profit": 0.02}}
    args = prepare_arguments(decision, CONFIG)
    _run(static_pipeline(brain.agent, decision, args))
    cfg = args["executor_config"]
    # same order as agent.py: normalization first — amount_quote becomes a
    # base-unit amount at the current price
    assert "amount_quote" not in cfg
    assert abs(cfg["amount"] - 200 / 80000.0) < 1e-12


def test_pipeline_grid_quote_clamped(tmp_path):
    brain = _make_brain(tmp_path)
    decision = {"symbol": "BTC", "action": "PROPOSE_GRID",
                "executor_config": {"total_amount_quote": 1000,
                                    "start_price": 1, "end_price": 2,
                                    "limit_price": 1.5,
                                    "triple_barrier_config": {"take_profit": 0.002}}}
    args = prepare_arguments(decision, CONFIG)
    report, _ = _run(static_pipeline(brain.agent, decision, args))
    # grid total_amount_quote survives normalization and IS clamped to the
    # position_notional_quote envelope (also trips the safety cap upstream)
    assert args["executor_config"]["total_amount_quote"] == 500
    assert any(e["path"] == "total_amount_quote" and e["clamped"]
               for e in report["events"])


# ------------------------------------------------------------ min confidence


def test_min_confidence_gate():
    floor = CONFIG
    assert check_min_confidence({"confidence": 0.7}, None, floor) is None
    assert check_min_confidence({}, None, floor) is None  # no conf = no gate
    rejection = check_min_confidence({"confidence": 0.55}, None, floor)
    assert "min_confidence" in rejection
    # a clamped gate_adjustments override lowers the floor
    eff = {"min_confidence": 0.5}
    assert check_min_confidence({"confidence": 0.55}, eff, floor) is None


# ----------------------------------------------------------------- execute


def test_execute_wait_records(tmp_path):
    brain = _make_brain(tmp_path)
    result = _run(brain.execute_decision(
        {"symbol": "BTC", "action": "WAIT", "regime": "ranging",
         "confidence": 0.6, "evidence": "chop, no edge"}, dry_run=True))
    assert result["outcome"] == "RECORDED"
    row = brain.analytics.get_decision_event(result["decision_id"])
    assert row["action"] == "WAIT"
    assert row["outcome_status"] == "RECORDED"
    assert row["mode"] == "PAPER"
    assert row["llm_model"] == "kimi-code"
    assert row["agent_version"] == "v1.5-kimi-manual"
    assert row["market_regime"] == "RANGING"  # normalized vocabulary
    # memory trail stays continuous with the old loop
    analyses = brain.memory.latest_analysis(symbol="BTC", limit=1)
    assert analyses and analyses[0]["action"] == "WAIT"


def test_execute_schema_rejection_records(tmp_path):
    brain = _make_brain(tmp_path)
    result = _run(brain.execute_decision({"action": "FLY"}, dry_run=True))
    assert result["outcome"] == "REJECTED"
    assert result["stage"] == "SCHEMA_INVALID"
    row = brain.analytics.get_decision_event(result["decision_id"])
    assert row["outcome_status"] == "REJECTED"
    assert "SCHEMA" in row["rejection_reason"]


def test_execute_dry_run_grid_records_without_creating(tmp_path):
    brain = _make_brain(tmp_path)  # gates stubbed to pass

    async def explode(cfg):  # must never be called in dry-run
        raise AssertionError("create_executor called during dry-run")

    brain.create_executor = explode
    result = _run(brain.execute_decision(
        {"symbol": "ETH", "action": "PROPOSE_GRID", "confidence": 0.7,
         "thesis": "tight range, low ADX", "evidence": "ADX 14, bb squeeze",
         "executor_config": {"total_amount_quote": 100,
                             "start_price": 3000, "end_price": 3200,
                             "limit_price": 3100,
                             "triple_barrier_config": {"take_profit": 0.002}}},
        dry_run=True))
    assert result["outcome"] == "DRY_RUN_PASSED"
    row = brain.analytics.get_decision_event(result["decision_id"])
    assert row["outcome_status"] == "RECORDED"
    assert row["risk_status"] == "PASSED"
    assert json.loads(row["market_context"])["dry_run"] is True
    assert row["tool_name"] == "manage_executors"


def test_execute_gate_rejection_records_adjustments(tmp_path):
    brain = _make_brain(
        tmp_path, validate_result=("ENTRY GATE — REJECTED: rsi 80 >= 75",
                                   "ENTRY_REJECTED"))
    result = _run(brain.execute_decision(
        {"symbol": "BTC", "action": "PROPOSE_POSITION", "confidence": 0.7,
         "thesis": "breakout continuation", "invalidation": "close < 80000",
         "executor_config": {"side": "LONG", "amount_quote": 200,
                             "take_profit": 0.02},
         "gate_adjustments": {"entry_gate.rsi_overbought":
                              {"value": 80, "reason": "ADX 42 trend"}}},
        dry_run=True))
    assert result["outcome"] == "REJECTED"
    assert result["stage"] == "ENTRY_REJECTED"
    row = brain.analytics.get_decision_event(result["decision_id"])
    assert row["outcome_status"] == "REJECTED"
    assert "ENTRY GATE" in row["rejection_reason"]
    ctx = json.loads(row["market_context"])
    assert ctx["thesis"] == "breakout continuation"
    assert ctx["invalidation"] == "close < 80000"
    applied = ctx["applied_gate_adjustments"]
    assert applied["overrides"]["entry_gate.rsi_overbought"] == 80


def test_execute_confidence_rejection(tmp_path):
    brain = _make_brain(tmp_path)  # gates would pass; conf floor stops it
    result = _run(brain.execute_decision(
        {"symbol": "BTC", "action": "PROPOSE_POSITION", "confidence": 0.55,
         "thesis": "t", "invalidation": "i",
         "executor_config": {"side": "LONG", "amount_quote": 200,
                             "take_profit": 0.02}},
        dry_run=True))
    assert result["outcome"] == "REJECTED"
    assert result["stage"] == "CONF_REJECTED"


def test_execute_real_creation_links_executor(tmp_path):
    brain = _make_brain(tmp_path)  # gates stubbed to pass

    async def fake_create(cfg):
        assert cfg["type"] == "position_executor"
        return {"executor_id": "abc123executor"}

    async def fake_portfolio():
        return {"equity": 10000.0}

    brain.create_executor = fake_create
    brain.portfolio_snapshot = fake_portfolio
    result = _run(brain.execute_decision(
        {"symbol": "BTC", "action": "PROPOSE_POSITION", "confidence": 0.8,
         "thesis": "trend", "invalidation": "close < 79000",
         "executor_config": {"side": "LONG", "amount_quote": 200,
                             "take_profit": 0.02}},
        dry_run=False))
    assert result["outcome"] == "EXECUTED"
    assert result["executor_id"] == "abc123executor"
    row = brain.analytics.get_decision_event(result["decision_id"])
    assert row["outcome_status"] == "EXECUTED"
    assert row["execution_id"] == "abc123executor"
    assert json.loads(row["portfolio_context"])["equity"] == 10000.0


def test_execute_create_failure_records_rejection(tmp_path):
    brain = _make_brain(tmp_path)

    async def boom(cfg):
        raise RuntimeError("API unreachable")

    brain.create_executor = boom
    result = _run(brain.execute_decision(
        {"symbol": "BTC", "action": "PROPOSE_POSITION", "confidence": 0.8,
         "thesis": "t", "invalidation": "i",
         "executor_config": {"side": "LONG", "amount_quote": 200,
                             "take_profit": 0.02}},
        dry_run=False))
    assert result["outcome"] == "ERROR"
    row = brain.analytics.get_decision_event(result["decision_id"])
    assert row["outcome_status"] == "REJECTED"
    assert row["risk_status"] == "EXECUTION_ERROR"
    assert "API unreachable" in row["rejection_reason"]
