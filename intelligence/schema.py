"""Unified Intelligence Schema (v0.4 Phase 3).

One schema for every non-market data dimension (on-chain, social, news,
narrative). Hard rules from the master prompt:

- Every item carries timestamp, source, confidence, relevance — the agent
  must always know whether information is 8 minutes or 8 days old.
- Raw text (posts, articles) never goes to the LLM. Only normalized,
  aggregated features/signals/events do.
- This is the analytics path: it must never block or break trading.

Storage lives in its own SQLite DB (data/intelligence.db), separate from
analytics.db, so intelligence ingestion can fail without touching trading
records.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# Signal kinds (extensible — providers map their data onto these)
SIGNAL_KINDS = {
    # social
    "social_volume", "social_volume_change", "sentiment", "sentiment_change",
    "social_dominance", "creator_activity", "trending_rank",
    # on-chain
    "exchange_inflow", "exchange_outflow", "whale_transfer",
    "active_addresses", "stablecoin_flow", "network_activity",
    # market-derived but intelligence-relevant
    "fear_greed",
    # narrative engine output is stored separately (Narrative)
}

NEWS_CATEGORIES = {
    "etf", "regulation", "listing", "delisting", "hack", "protocol_upgrade",
    "partnership", "macro", "exchange", "general",
}


@dataclass
class IntelSignal:
    """One normalized observation from an external source."""
    kind: str                    # from SIGNAL_KINDS
    symbol: str | None           # None = market-wide (e.g. fear & greed)
    source: str                  # provider name, e.g. "lunarcrush"
    value: float | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5      # 0-1, provider/data-quality confidence
    relevance: float = 0.5       # 0-1, relevance to the symbol
    ts: float = field(default_factory=time.time)

    def age_minutes(self, now: float | None = None) -> float:
        return max(0.0, ((now or time.time()) - self.ts) / 60.0)


@dataclass
class NewsEvent:
    """One normalized news item. `summary` is the provider's short text or
    our condensed version — never the full article body."""
    title: str
    source: str
    summary: str = ""
    asset: str | None = None
    category: str = "general"
    severity: float = 0.5        # 0-1, expected market impact
    relevance: float = 0.5
    url: str = ""
    ts: float = field(default_factory=time.time)

    def age_minutes(self, now: float | None = None) -> float:
        return max(0.0, ((now or time.time()) - self.ts) / 60.0)


@dataclass
class Narrative:
    """Output of the Narrative Engine: what the market is currently about.

    A narrative explains what is happening — it is never a trade signal by
    itself (master prompt §11).
    """
    name: str                    # e.g. "institutional_btc_accumulation"
    symbol: str | None = None
    strength: float = 0.0        # overall narrative strength 0-1
    novelty: float = 0.0         # how new vs. persistent 0-1
    social_momentum: float = 0.0
    price_confirmation: float = 0.0
    onchain_confirmation: float = 0.0
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)


@dataclass
class IntelSnapshot:
    """The compact, structured bundle handed to the LLM. Contains only
    aggregated features/signals — never raw posts or articles."""
    generated_at: float = field(default_factory=time.time)
    social: dict[str, Any] = field(default_factory=dict)    # per symbol
    onchain: dict[str, Any] = field(default_factory=dict)   # per symbol
    news: list[dict[str, Any]] = field(default_factory=list)  # recent events
    narratives: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)  # provider health etc.

    def for_llm(self, max_news: int = 5, max_narratives: int = 3) -> dict[str, Any]:
        """Compact JSON-ready view with explicit data ages."""
        now = time.time()

        def with_age(item: dict[str, Any]) -> dict[str, Any]:
            ts = item.get("ts")
            if ts:
                item = dict(item)
                item["age_minutes"] = round(max(0.0, (now - ts) / 60.0), 1)
            return item

        return {
            "generated_at": self.generated_at,
            "social": self.social,
            "onchain": self.onchain,
            "news": [with_age(n) for n in self.news[:max_news]],
            "narratives": [with_age(n) for n in self.narratives[:max_narratives]],
            "meta": self.meta,
        }
