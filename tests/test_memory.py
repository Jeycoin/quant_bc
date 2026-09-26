"""MemoryStore tests — decisions, reviews, notes round-trip."""

from agent.memory.store import MemoryStore


def test_decision_lifecycle(tmp_path):
    store = MemoryStore(str(tmp_path / "mem.db"))
    did = store.record_decision(
        mode="PAPER",
        market_context="btc ranging",
        analysis="no trade",
        tool_name="get_market_data",
        tool_arguments={"data_type": "prices"},
        outcome="OK",
    )
    store.update_outcome(did, "OK — reviewed")

    recent = store.recent_decisions(5)
    assert len(recent) == 1
    assert recent[0]["id"] == did
    assert recent[0]["outcome"] == "OK — reviewed"
    assert recent[0]["tool_arguments"] == {"data_type": "prices"}
    assert recent[0]["mode"] == "PAPER"
    store.close()


def test_reviews_and_notes(tmp_path):
    store = MemoryStore(str(tmp_path / "mem.db"))
    rid = store.record_review("grid #1", "thesis held, spread captured")
    nid = store.record_note("BTC regime", "low volatility compression")
    assert rid == 1 and nid == 1

    rows = store._conn.execute("SELECT subject, review FROM trade_reviews").fetchall()
    assert rows == [("grid #1", "thesis held, spread captured")]
    rows = store._conn.execute("SELECT topic, note FROM research_notes").fetchall()
    assert rows == [("BTC regime", "low volatility compression")]
    store.close()


def test_blocked_decisions_are_recorded(tmp_path):
    store = MemoryStore(str(tmp_path / "mem.db"))
    store.record_decision(
        mode="PAPER",
        market_context="",
        analysis="",
        tool_name="manage_executors",
        tool_arguments={"action": "create"},
        outcome="BLOCKED: PAPER mode violation",
    )
    assert store.recent_decisions(1)[0]["outcome"].startswith("BLOCKED")
    store.close()
