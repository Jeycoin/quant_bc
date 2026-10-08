"""Snapshot builder: store -> features -> narratives -> IntelSnapshot.

This is the single entry point used by the agent (LLM context) and the
dashboard. It runs the narrative engine and records detected narratives
so narrative history (novelty, replay) accumulates.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from intelligence.features import (
    build_derivatives_features,
    build_news_features,
    build_onchain_features,
    build_social_features,
)
from intelligence.narrative import NarrativeEngine
from intelligence.schema import IntelSnapshot
from intelligence.store import IntelligenceStore


def build_snapshot(
    store: IntelligenceStore,
    symbols: list[str],
    market: dict[str, dict[str, Any]] | None = None,
    record_narratives: bool = True,
) -> IntelSnapshot:
    """market: per-symbol {"24h_change_pct": ...} from the caller's market
    data (the intelligence layer never fetches prices itself)."""
    social = build_social_features(store, symbols)
    onchain = build_onchain_features(store, symbols)
    derivatives = build_derivatives_features(store, symbols)
    news = build_news_features(store, symbols)

    engine = NarrativeEngine()
    narratives = []
    for symbol in symbols:
        detected = engine.detect(
            symbol,
            market=(market or {}).get(symbol, {}),
            social=social, onchain=onchain, news=news,
            store=store,
        )
        for n in detected:
            if record_narratives:
                store.record_narrative(n)
            narratives.append(asdict(n))

    return IntelSnapshot(
        social=social,
        onchain=onchain,
        derivatives=derivatives,
        news=news["top_events"],
        narratives=narratives,
        meta={"news_volume": {s: (news["symbols"].get(s) or {})
                              for s in symbols}},
    )
