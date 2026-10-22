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
import time
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


# ------------------------------------------------------------ data quality


def test_data_quality_flags_stale_and_missing():
    intel = {
        "social": {
            "market": {"fear_greed": {"value": 68, "age_minutes": 551.0}},
            "symbols": {"BTC": {}, "ETH": {}},
        },
        "onchain": {
            "BTC": {"network_activity": {"value": 1, "age_minutes": 1978.0}},
            "ETH": {},
        },
        "derivatives": {
            "BTC": {"open_interest": {"value": 1, "age_minutes": 9.0},
                    "funding_rate": {"value": 0.1, "age_minutes": 9.0}},
        },
        "news": [{"title": "x", "age_minutes": 30.0}],
        "narratives": [],
    }
    q = kimi_brain.assess_data_quality(intel)
    stale_sections = {s["section"] for s in q["stale"]}
    assert "social.fear_greed" in stale_sections
    assert "onchain.BTC.network_activity" in stale_sections
    fg = next(s for s in q["stale"] if s["section"] == "social.fear_greed")
    assert fg["age_minutes"] == 551.0 and fg["threshold"] == 360
    # fresh derivatives and news are NOT flagged
    assert not any(s.startswith("derivatives") for s in stale_sections)
    assert not any(s.startswith("news") for s in stale_sections)
    assert "social.symbols.*" in q["missing"]
    assert "onchain.ETH.*" in q["missing"]
    assert "narratives" in q["missing"]


def test_data_quality_all_fresh_is_quiet():
    intel = {
        "social": {"market": {"fear_greed": {"value": 50, "age_minutes": 30}},
                   "symbols": {"BTC": {"sentiment": {"age_minutes": 20}}}},
        "onchain": {"BTC": {"network_activity": {"age_minutes": 100}}},
        "derivatives": {"BTC": {"open_interest": {"age_minutes": 5},
                                "funding_rate": {"age_minutes": 5}}},
        "news": [{"age_minutes": 10}],
        "narratives": [{"name": "n", "age_minutes": 30}],
    }
    q = kimi_brain.assess_data_quality(intel)
    assert q["stale"] == []
    assert q["missing"] == []


def test_data_quality_no_intel():
    q = kimi_brain.assess_data_quality(None)
    assert q == {"stale": [], "missing": ["intelligence.*"]}


# ------------------------------------------------------- live/replay split


def _seed_trades(analytics):
    # live fills (real executor ids)
    analytics.upsert_trade_event(
        executor_id="4LElive1", symbol="BTC", side="LONG", status="CLOSED",
        close_type="STOP_LOSS", pnl_quote=-5.0, ts_close=1_000.0)
    analytics.upsert_trade_event(
        executor_id="4LElive2", symbol="BTC", side="LONG", status="CLOSED",
        close_type="TAKE_PROFIT", pnl_quote=8.0, ts_close=2_000.0)
    # replay fills (bt-* ids; replay also uses regular close types)
    for i, pnl in enumerate((-1.0, 2.0, -3.0, 1.5)):
        analytics.upsert_trade_event(
            executor_id=f"bt-replay{i}", symbol="BTC", side="SHORT",
            status="CLOSED", close_type="STOP_LOSS", pnl_quote=pnl,
            ts_close=3_000.0 + i)
    analytics.upsert_trade_event(
        executor_id="bt-replayX", symbol="BTC", side="LONG", status="CLOSED",
        close_type="REPLAY_END", pnl_quote=0.5, ts_close=4_000.0)


def test_trade_split_tags_and_separates(tmp_path):
    brain = _make_brain(tmp_path)
    _seed_trades(brain.analytics)
    entry = {}
    brain._apply_trade_split(entry, "BTC")
    recent = entry["recent_trades"]
    assert recent and all("src" in t for t in recent)
    assert recent[0]["src"] == "replay"  # newest is the REPLAY_END row
    tr = entry["track_record"]
    assert tr["live"]["trades"] == 2
    assert tr["live"]["total_pnl"] == 3.0
    assert tr["live"]["note"]  # < 3 live trades: no fake win rate
    assert "win_rate" not in tr["live"]
    assert tr["replay"]["trades"] == 5
    assert tr["replay"]["win_rate"] == 0.6


def test_trade_split_zero_live_is_explicit(tmp_path):
    brain = _make_brain(tmp_path)
    analytics = brain.analytics
    analytics.upsert_trade_event(
        executor_id="bt-only", symbol="ETH", side="LONG", status="CLOSED",
        close_type="REPLAY_END", pnl_quote=1.0, ts_close=1_000.0)
    entry = {}
    brain._apply_trade_split(entry, "ETH")
    assert entry["track_record"]["live"] == {"trades": 0}
    assert entry["track_record"]["replay"]["trades"] == 1


# ---------------------------------------------------------------- follow-up


def test_ref_price_schema_validation():
    assert validate_decision({"symbol": "BTC", "action": "WAIT",
                              "ref_price": 81782.0}) == []
    errors = validate_decision({"symbol": "BTC", "action": "WAIT",
                                "ref_price": -5})
    assert any("ref_price" in e for e in errors)


def test_ref_price_recorded_in_market_context(tmp_path):
    brain = _make_brain(tmp_path)
    result = _run(brain.execute_decision(
        {"symbol": "BTC", "action": "WAIT", "ref_price": 81782.0},
        dry_run=True))
    row = brain.analytics.get_decision_event(result["decision_id"])
    assert json.loads(row["market_context"])["ref_price"] == 81782.0


def _seed_kimi_decision(analytics, symbol, action, *, side=None,
                        outcome="RECORDED", ref_price=None, age_s=1800,
                        thesis="t" * 200, invalidation="i" * 200,
                        evidence=None, dry_run=False):
    mc = {"thesis": thesis, "invalidation": invalidation}
    if ref_price is not None:
        mc["ref_price"] = ref_price
    if dry_run:
        mc["dry_run"] = True
    ta = {"executor_config": {"side": side}} if side else None
    analytics.record_decision_event(
        mode="PAPER", symbol=symbol, action=action, outcome_status=outcome,
        market_context=mc, tool_arguments=ta, evidence=evidence,
        llm_model="kimi-code",
        ts=time.time() - age_s)


def test_follow_up_wait_flat_market(tmp_path):
    brain = _make_brain(tmp_path)
    _seed_kimi_decision(brain.analytics, "BTC", "WAIT", ref_price=81800.0)
    fu = brain._follow_up(["BTC"], {"BTC": {"price": 81770.0}}, {})
    e = fu["BTC"]
    assert e["change_pct"] == -0.04
    assert "横盘" in e["verdict"] and "暂未被证伪" in e["verdict"]
    assert e["thesis"] == "t" * 120  # truncated
    assert e["invalidation"] == "i" * 120
    assert e["age_min"] == 30.0


def test_follow_up_directional_verdicts(tmp_path):
    brain = _make_brain(tmp_path)
    # LONG that went the right way -> validated
    _seed_kimi_decision(brain.analytics, "BTC", "PROPOSE_POSITION",
                        side="LONG", outcome="EXECUTED", ref_price=80000.0)
    fu = brain._follow_up(["BTC"], {"BTC": {"price": 80500.0}}, {})
    assert "判断被验证" in fu["BTC"]["verdict"]
    # SHORT that went against -> falsified, not executed -> suffix
    _seed_kimi_decision(brain.analytics, "ETH", "PROPOSE_POSITION",
                        side="SHORT", outcome="REJECTED", ref_price=3000.0)
    fu = brain._follow_up(["ETH"], {"ETH": {"price": 3030.0}}, {})
    assert "判断被证伪" in fu["ETH"]["verdict"]
    assert "(未成交)" in fu["ETH"]["verdict"]


def test_follow_up_grid_and_no_ref_price(tmp_path):
    brain = _make_brain(tmp_path)
    _seed_kimi_decision(brain.analytics, "SOL", "PROPOSE_GRID",
                        outcome="EXECUTED", ref_price=100.0)
    fu = brain._follow_up(["SOL"], {"SOL": {"price": 100.2}}, {})
    assert "区间假设暂成立" in fu["SOL"]["verdict"]
    _seed_kimi_decision(brain.analytics, "XRP", "WAIT")  # no ref_price
    fu = brain._follow_up(["XRP"], {"XRP": {"price": 1.3}}, {})
    assert "无法跟踪" in fu["XRP"]["verdict"]


def test_follow_up_reports_open_position_pnl(tmp_path):
    brain = _make_brain(tmp_path)
    _seed_kimi_decision(brain.analytics, "BTC", "WAIT", ref_price=84000.0)
    portfolio = {"open_positions": [
        {"trading_pair": "BTC-USDT", "amount": 0.0012,
         "unrealized_pnl": -2.54}]}
    fu = brain._follow_up(["BTC"], {"BTC": {"price": 81770.0}}, portfolio)
    assert fu["BTC"]["open_position_pnl"] == -2.54


def test_follow_up_ignores_other_models(tmp_path):
    brain = _make_brain(tmp_path)
    brain.analytics.record_decision_event(
        mode="PAPER", symbol="BTC", action="SCAN", llm_model="glm-4.6",
        ts=1_800_000_000.0)
    assert brain._follow_up(["BTC"], {"BTC": {"price": 1.0}}, {}) == {}


def test_follow_up_skips_smoke_evidence(tmp_path):
    brain = _make_brain(tmp_path)
    _seed_kimi_decision(brain.analytics, "ETH", "WAIT", ref_price=1900.0,
                        age_s=60, evidence="batch smoke test")
    _seed_kimi_decision(brain.analytics, "ETH", "WATCH", ref_price=1850.0,
                        age_s=3600, evidence="legit cycle decision")
    fu = brain._follow_up(["ETH"], {"ETH": {"price": 1860.0}}, {})
    # the newer smoke row (ref 1900) is skipped; the legit older row wins
    assert fu["ETH"]["ref_price"] == 1850.0
    assert fu["ETH"]["action"] == "WATCH"


def test_follow_up_skips_dry_run_rows(tmp_path):
    brain = _make_brain(tmp_path)
    _seed_kimi_decision(brain.analytics, "SOL", "PROPOSE_GRID",
                        outcome="RECORDED", ref_price=100.0, age_s=60,
                        evidence="plausible-looking", dry_run=True)
    fu = brain._follow_up(["SOL"], {"SOL": {"price": 101.0}}, {})
    # only a dry-run row exists -> no follow-up at all for the symbol
    assert "SOL" not in fu


def test_follow_up_wait_direction_aware(tmp_path):
    brain = _make_brain(tmp_path)
    # price dropped after WAIT -> the caution was correct
    _seed_kimi_decision(brain.analytics, "BTC", "WAIT", ref_price=100.0)
    fu = brain._follow_up(["BTC"], {"BTC": {"price": 99.5}}, {})
    assert "观望正确" in fu["BTC"]["verdict"]
    assert "规避下跌" in fu["BTC"]["verdict"]
    # price rose after WATCH -> opportunity cost
    _seed_kimi_decision(brain.analytics, "ETH", "WATCH", ref_price=100.0)
    fu = brain._follow_up(["ETH"], {"ETH": {"price": 100.5}}, {})
    assert "错过反弹" in fu["ETH"]["verdict"]
    assert "复核是否该更积极" in fu["ETH"]["verdict"]


# -------------------------------------------------------------------- batch


def test_execute_all_processes_every_decision(tmp_path):
    brain = _make_brain(tmp_path)
    results = _run(brain.execute_all([
        {"symbol": "BTC", "action": "WAIT", "ref_price": 81800.0},
        {"symbol": "ETH", "action": "WATCH"},
        {"symbol": "SOL", "action": "WAIT"},
    ], dry_run=True))
    assert [r["outcome"] for r in results] == ["RECORDED"] * 3
    assert len({r["decision_id"] for r in results}) == 3
    rows = [brain.analytics.get_decision_event(r["decision_id"])
            for r in results]
    assert all(row["outcome_status"] == "RECORDED" for row in rows)


# ----------------------------------------- market_features outage hardening


def _synth_candles(n=60, start=1_700_000_000.0):
    return [{"timestamp": start + i * 1800, "open": 100 + i * 0.1,
             "high": 101 + i * 0.1, "low": 99 + i * 0.1,
             "close": 100.5 + i * 0.1, "volume": 10 + i}
            for i in range(n)]


def _wire_feature_stubs(brain, failing=()):
    """Stub the agent's market-data seams; symbols in `failing` raise on
    the public-candle fallback (HB returns empty -> fallback is always
    exercised)."""
    agent = brain.agent

    async def hb_rest(method, path, payload):
        return {}  # no collected candles -> public fallback path

    async def public_candles(coin, interval, hours=200):
        if coin in failing:
            raise RuntimeError(f"intermittent venue hiccup on {coin}")
        return _synth_candles()

    async def public_derivatives():
        return {"BTC": {"funding": 0.0001}}

    agent._hb_rest = hb_rest
    agent._public_candles = public_candles
    agent._public_derivatives = public_derivatives
    return agent


def test_features_single_symbol_failure_isolated(tmp_path, capsys):
    brain = _make_brain(tmp_path)
    agent = _wire_feature_stubs(brain, failing={"SOL"})
    errors = {}
    out = _run(agent._market_features_context(errors=errors))
    assert out is not None
    assert "SOL" not in out                      # the failed symbol is absent
    assert set(out) == {"BTC", "ETH", "XRP", "SUI"}  # the rest survive
    assert "public_candles" in errors["SOL"]
    assert "intermittent" in errors["SOL"]
    err = capsys.readouterr().err
    assert "[warn] market_features SOL" in err
    assert "public_candles" in err


def test_features_all_fail_returns_none(tmp_path, capsys):
    brain = _make_brain(tmp_path)
    agent = _wire_feature_stubs(
        brain, failing={"BTC", "ETH", "SOL", "XRP", "SUI"})
    errors = {}
    out = _run(agent._market_features_context(errors=errors))
    assert out is None                            # total outage -> None
    assert set(errors) == {"BTC", "ETH", "SOL", "XRP", "SUI"}
    assert capsys.readouterr().err.count("[warn] market_features") >= 5


def test_features_errors_param_optional(tmp_path):
    brain = _make_brain(tmp_path)
    agent = _wire_feature_stubs(brain, failing={"SOL"})
    # legacy call shape (no errors argument) keeps working
    out = _run(agent._market_features_context())
    assert set(out) == {"BTC", "ETH", "XRP", "SUI"}


def test_build_context_reports_missing_symbols(tmp_path):
    brain = _make_brain(tmp_path)
    agent = brain.agent

    async def mf(errors=None):
        if errors is not None:
            errors["SOL"] = "public_candles: boom"
        return {"BTC": {"price": 1.0}}

    async def empty(*a, **kw):
        return None

    agent._market_features_context = mf
    agent._snapshot_portfolio = empty
    agent._intel_context = empty
    agent._memory_context = empty

    async def no_executors():
        return []

    async def no_grid(sym):
        return None

    brain.running_executors = no_executors
    brain._context_grid_state = no_grid
    ctx = _run(brain.build_context())
    assert ctx["market_features"] == {"BTC": {"price": 1.0}}
    assert ctx["market_features_missing"] == ["ETH", "SOL", "XRP", "SUI"]
    assert ctx["market_features_errors"] == {"SOL": "public_candles: boom"}


def test_build_context_full_house_reports_no_missing(tmp_path):
    brain = _make_brain(tmp_path)
    agent = brain.agent

    async def mf(errors=None):
        return {s: {"price": 1.0} for s in
                ("BTC", "ETH", "SOL", "XRP", "SUI")}

    async def empty(*a, **kw):
        return None

    agent._market_features_context = mf
    agent._snapshot_portfolio = empty
    agent._intel_context = empty
    agent._memory_context = empty

    async def no_executors():
        return []

    async def no_grid(sym):
        return None

    brain.running_executors = no_executors
    brain._context_grid_state = no_grid
    ctx = _run(brain.build_context())
    assert ctx["market_features_missing"] == []
    assert ctx["market_features_errors"] == {}
