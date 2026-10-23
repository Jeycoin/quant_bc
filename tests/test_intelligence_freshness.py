"""Tests for intelligence freshness honesty (collector stale reporting)
and narrative time-decay (regulatory_shock must not linger at full
strength off a stale headline).
"""

import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import intelligence.collector as collector  # noqa: E402
from intelligence.narrative import NarrativeEngine  # noqa: E402
from intelligence.schema import IntelSignal  # noqa: E402
from intelligence.store import IntelligenceStore  # noqa: E402


class _FakeFng:
    """Stands in for alternative.me: fetch succeeds but the source
    timestamp only advances when the source actually updates."""
    name = "fake_fng"

    def __init__(self, ts: float):
        self.ts = ts

    def available(self) -> bool:
        return True

    async def fetch(self, symbols):
        return {"signals": [IntelSignal(
            kind="fear_greed", symbol=None, source=self.name,
            value=50.0, ts=self.ts)], "news": []}


def test_collector_reports_stale_when_source_not_advancing(
        tmp_path, monkeypatch):
    store = IntelligenceStore(str(tmp_path / "intel.db"))
    provider = _FakeFng(time.time() - 3600)
    monkeypatch.setattr(collector, "ALL_PROVIDERS", [provider])
    try:
        meta = asyncio.run(collector.collect_once(store, ["BTC"]))
        assert meta["providers"]["fake_fng"]["status"] == "ok"  # first write
        meta = asyncio.run(collector.collect_once(store, ["BTC"]))
        entry = meta["providers"]["fake_fng"]
        assert entry["status"] == "stale"  # same source ts: honest report
        assert "unchanged" in entry["reason"]
        provider.ts = time.time()  # source updates -> recovers
        meta = asyncio.run(collector.collect_once(store, ["BTC"]))
        assert meta["providers"]["fake_fng"]["status"] == "ok"
    finally:
        store.close()


def _regulation_news(age_min: float) -> dict:
    return {"symbols": {"BTC": {
        "news_volume_24h": 3, "max_severity_24h": 0.9,
        "categories": ["regulation"],
        "category_age_min": {"regulation": age_min}}}}


def test_regulatory_shock_decays_with_headline_age():
    engine = NarrativeEngine()
    fresh = engine.detect("BTC", market={}, social={}, onchain={},
                          news=_regulation_news(30))
    shock = [n for n in fresh if n.name == "regulatory_shock"]
    assert shock and shock[0].strength > 0.7  # fresh: near full strength

    half_life = engine.detect("BTC", market={}, social={}, onchain={},
                              news=_regulation_news(4 * 60))
    shock = [n for n in half_life if n.name == "regulatory_shock"]
    assert shock and 0.3 <= shock[0].strength <= 0.5  # decayed, not 0.9

    stale = engine.detect("BTC", market={}, social={}, onchain={},
                          news=_regulation_news(10 * 60))
    assert [n for n in stale if n.name == "regulatory_shock"] == []
