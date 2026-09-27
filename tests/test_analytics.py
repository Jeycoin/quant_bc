"""Tests for the AI Quant Lab analytics store, versioning and agent capture."""

import json
import time

import pytest

from agent.agent import extract_executor_id, normalize_regime
from analytics.store import AnalyticsStore, new_decision_id
from analytics.versioning import _file_hash, current_versions


@pytest.fixture
def store(tmp_path):
    s = AnalyticsStore(str(tmp_path / "analytics.db"))
    yield s
    s.close()


# --------------------------------------------------------------- store: schema

def test_schema_created_idempotent(tmp_path):
    path = str(tmp_path / "a.db")
    AnalyticsStore(path).close()
    s = AnalyticsStore(path)  # second open must not fail
    s.close()


def test_record_and_get_decision_event(store):
    did = store.record_decision_event(
        mode="PAPER",
        symbol="BTC",
        market_regime="RANGING",
        action="WAIT",
        strategy="grid",
        confidence=0.65,
        evidence="price inside 24h range",
        risk_status="PASSED",
        outcome_status="RECORDED",
        market_context={"tool_result": "price 84000"},
        portfolio_context={"equity": 10000.0, "open_position_count": 0},
        agent_version="0.2.0",
        prompt_version="abc12345",
        config_version="def67890",
    )
    row = store.get_decision_event(did)
    assert row is not None
    assert row["decision_id"] == did
    assert row["symbol"] == "BTC"
    assert row["market_regime"] == "RANGING"
    assert row["action"] == "WAIT"
    assert row["confidence"] == 0.65
    assert json.loads(row["portfolio_context"])["equity"] == 10000.0
    assert row["prompt_version"] == "abc12345"


def test_link_execution(store):
    did = store.record_decision_event(mode="PAPER", action="TRADE")
    store.link_execution(did, "4LECR7jFVaL1C9FdtvHD8wJuzdo1NDBaTTy43zcycjFq")
    row = store.get_decision_event(did)
    assert row["execution_id"] == "4LECR7jFVaL1C9FdtvHD8wJuzdo1NDBaTTy43zcycjFq"


def test_upsert_trade_event_roundtrip(store):
    store.upsert_trade_event(
        executor_id="exec1", symbol="BTC-USDT", strategy="grid_executor",
        pnl_quote=1.5, regime_at_entry="RANGING", source="agent",
    )
    store.upsert_trade_event(executor_id="exec1", pnl_quote=2.0)  # replace
    row = store._conn.execute(
        "SELECT pnl_quote, source FROM trade_events WHERE executor_id='exec1'"
    ).fetchone()
    assert row == (2.0, None)  # INSERT OR REPLACE overwrites all columns


def test_review_event(store):
    rid = store.record_review_event(
        execution_id="exec1", outcome="+0.18 USDT",
        decision_quality="good", lesson="grid works in ranging markets",
    )
    assert rid >= 1


# ------------------------------------------------------------ store: experiments

def test_experiment_lifecycle(store):
    eid = store.start_experiment(
        name="7d-testnet", symbols=["BTC", "ETH"], strategies=["grid"],
        agent_version="0.2.0",
    )
    active = store.get_active_experiment()
    assert active is not None
    assert active["experiment_id"] == eid
    assert active["status"] == "running"
    store.end_experiment(eid)
    assert store.get_active_experiment() is None


def test_decision_event_tagged_with_experiment(store):
    eid = store.start_experiment(name="exp")
    did = store.record_decision_event(mode="PAPER", action="WAIT", experiment_id=eid)
    assert store.get_decision_event(did)["experiment_id"] == eid


# ------------------------------------------------------------------ versioning

def test_file_hash_changes_with_content(tmp_path):
    f = tmp_path / "p.md"
    f.write_text("a", encoding="utf-8")
    h1 = _file_hash(f)
    f.write_text("b", encoding="utf-8")
    assert _file_hash(f) != h1
    assert _file_hash(tmp_path / "missing.md") == "unknown"


def test_current_versions_keys():
    v = current_versions()
    assert set(v) == {"agent_version", "prompt_version", "config_version"}
    assert all(isinstance(x, str) and x for x in v.values())


# ----------------------------------------------------------- agent helpers

def test_normalize_regime_standard_and_aliases():
    assert normalize_regime("RANGING") == "RANGING"
    assert normalize_regime("trending_up") == "TRENDING_BULL"
    assert normalize_regime("trending-down") == "TRENDING_BEAR"
    assert normalize_regime("volatile") == "HIGH_VOLATILITY"
    assert normalize_regime("something weird") == "UNCERTAIN"
    assert normalize_regime(None) is None


def test_extract_executor_id():
    eid = "4LECR7jFVaL1C9FdtvHD8wJuzdo1NDBaTTy43zcycjFq"
    assert extract_executor_id(f"Executor created with ID: {eid}") == eid
    assert extract_executor_id(json.dumps({"executor_id": eid})) == eid
    assert extract_executor_id("no id here") is None
    assert extract_executor_id("") is None


def test_new_decision_id_unique():
    assert new_decision_id() != new_decision_id()


def test_decision_event_ts_default(store):
    before = time.time()
    did = store.record_decision_event(mode="PAPER")
    assert store.get_decision_event(did)["ts"] >= before
