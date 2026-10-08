"""Intelligence collector: run all available providers, store results.

Fail-independent: each provider's errors are caught and reported in the
health meta; the collector itself never raises. Designed to be called from
a cron/loop (scripts/collect_intelligence.py) or ad-hoc.
"""

from __future__ import annotations

import time
from typing import Any

from intelligence.providers import ALL_PROVIDERS
from intelligence.schema import IntelSignal, NewsEvent
from intelligence.store import IntelligenceStore


async def collect_once(
    store: IntelligenceStore,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """One collection round. Returns per-provider health for diagnostics."""
    symbols = symbols or ["BTC", "ETH", "SOL", "XRP", "SUI"]
    meta: dict[str, Any] = {"collected_at": time.time(), "providers": {}}
    for provider in ALL_PROVIDERS:
        if not provider.available():
            meta["providers"][provider.name] = {"status": "disabled",
                                                "reason": "no API key"}
            continue
        try:
            result = await provider.fetch(symbols)
            signals: list[IntelSignal] = result.get("signals", [])
            news: list[NewsEvent] = result.get("news", [])
            if signals:
                store.record_signals(signals)
            inserted = sum(1 for n in news if store.record_news(n))
            meta["providers"][provider.name] = {
                "status": "ok", "signals": len(signals),
                "news_new": inserted, "news_seen": len(news),
            }
        except Exception as exc:
            meta["providers"][provider.name] = {"status": "error",
                                                "reason": str(exc)[:200]}
    return meta
