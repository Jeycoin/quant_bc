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
            # freshness honesty: a successful fetch whose newest signal
            # timestamp did not advance means the SOURCE data is stale
            # (e.g. alternative.me updates F&G once a day) — record the
            # row for history but report "stale", not "ok"
            prev_ts = (store.latest_signal_ts(provider.name)
                       if signals else None)
            if signals:
                store.record_signals(signals)
            inserted = sum(1 for n in news if store.record_news(n))
            entry = {"status": "ok", "signals": len(signals),
                     "news_new": inserted, "news_seen": len(news)}
            if (signals and prev_ts is not None
                    and max(s.ts for s in signals) <= prev_ts):
                entry["status"] = "stale"
                entry["reason"] = ("source data unchanged "
                                   "(provider timestamp not advancing)")
            meta["providers"][provider.name] = entry
        except Exception as exc:
            meta["providers"][provider.name] = {"status": "error",
                                                "reason": str(exc)[:200]}
    return meta
