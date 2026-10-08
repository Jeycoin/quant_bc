"""Feature Engine (v0.4 Phase 5).

Aggregates stored raw signals into compact, LLM-ready features. All output
carries data ages. Deterministic — no LLM involved.
"""

from __future__ import annotations

import json
import time
from typing import Any

from intelligence.store import IntelligenceStore


def _latest(signals: list[dict[str, Any]], kind: str,
            symbol: str | None = None) -> dict[str, Any] | None:
    for s in signals:  # recent_signals returns newest first
        if s["kind"] == kind and (symbol is None or s["symbol"] == symbol):
            return s
    return None


def _age(ts: float | None, now: float) -> float | None:
    return round(max(0.0, (now - ts) / 60.0), 1) if ts else None


def build_social_features(store: IntelligenceStore,
                          symbols: list[str]) -> dict[str, Any]:
    """Social features per symbol + market-wide sentiment."""
    now = time.time()
    signals = store.recent_signals(hours=48)
    out: dict[str, Any] = {"market": {}, "symbols": {}}

    fng = _latest(signals, "fear_greed")
    if fng:
        try:
            payload = json.loads(fng.get("payload") or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        out["market"]["fear_greed"] = {
            "value": fng["value"],
            "classification": payload.get("classification"),
            "age_minutes": _age(fng["ts"], now),
        }
    chg = _latest(signals, "sentiment_change")
    if chg:
        out["market"]["sentiment_change_1d"] = {
            "value": chg["value"], "age_minutes": _age(chg["ts"], now)}

    for symbol in symbols:
        entry: dict[str, Any] = {}
        for kind in ("social_volume", "sentiment", "social_dominance",
                     "creator_activity"):
            s = _latest(signals, kind, symbol)
            if s:
                entry[kind] = {"value": s["value"],
                               "age_minutes": _age(s["ts"], now)}
        out["symbols"][symbol] = entry
    return out


def build_onchain_features(store: IntelligenceStore,
                           symbols: list[str]) -> dict[str, Any]:
    now = time.time()
    signals = store.recent_signals(hours=72)
    out: dict[str, Any] = {}
    for symbol in symbols:
        entry: dict[str, Any] = {}
        for kind in ("exchange_inflow", "exchange_outflow", "active_addresses",
                     "network_activity", "whale_transfer", "stablecoin_flow"):
            s = _latest(signals, kind, symbol)
            if not s:
                continue
            item: dict[str, Any] = {"value": s["value"],
                                    "age_minutes": _age(s["ts"], now)}
            try:
                payload = json.loads(s.get("payload") or "{}")
                if "change_pct_1d" in payload:
                    item["change_pct_1d"] = payload["change_pct_1d"]
            except (json.JSONDecodeError, TypeError):
                pass
            entry[kind] = item
        out[symbol] = entry
    return out


def build_derivatives_features(store: IntelligenceStore,
                               symbols: list[str]) -> dict[str, Any]:
    """Open interest + funding per symbol, with an OI trend read.

    The OI change is computed against the observation ~24h ago (collector
    runs every 15 min, so the store holds a dense series). Price/OI
    divergence interpretation lives with the LLM — here we only ship the
    numbers and their ages.
    """
    now = time.time()
    signals = store.recent_signals(hours=48)
    out: dict[str, Any] = {}
    for symbol in symbols:
        entry: dict[str, Any] = {}
        for kind in ("open_interest", "funding_rate"):
            s = _latest(signals, kind, symbol)
            if s:
                entry[kind] = {"value": s["value"],
                               "age_minutes": _age(s["ts"], now)}
        oi_series = [s for s in signals
                     if s["kind"] == "open_interest" and s["symbol"] == symbol]
        if len(oi_series) >= 2:
            latest = oi_series[0]  # recent_signals returns newest first
            ref = None
            for s in oi_series[1:]:
                if latest["ts"] - s["ts"] >= 23 * 3600:
                    ref = s
                    break
            if ref is None:
                ref = oi_series[-1]
            if ref["value"]:
                entry["oi_change_pct"] = round(
                    (latest["value"] / ref["value"] - 1) * 100, 2)
                entry["oi_change_window_h"] = round(
                    (latest["ts"] - ref["ts"]) / 3600, 1)
        out[symbol] = entry
    return out


def build_news_features(store: IntelligenceStore,
                        symbols: list[str]) -> dict[str, Any]:
    """News volume / severity per asset over 24h + the top recent events."""
    now = time.time()
    out: dict[str, Any] = {"symbols": {}, "top_events": []}
    for symbol in symbols:
        events = store.recent_news(asset=symbol, hours=24, limit=50)
        asset_events = [e for e in events if e["asset"] == symbol]
        out["symbols"][symbol] = {
            "news_volume_24h": len(asset_events),
            "high_severity_24h": sum(1 for e in asset_events
                                     if (e["severity"] or 0) >= 0.75),
            "max_severity_24h": max((e["severity"] or 0 for e in asset_events),
                                    default=0),
            "categories": sorted({e["category"] for e in asset_events}),
        }
    top = sorted(store.recent_news(hours=12, limit=50),
                 key=lambda e: (e["severity"] or 0), reverse=True)[:5]
    out["top_events"] = [{
        "title": e["title"], "asset": e["asset"], "category": e["category"],
        "severity": e["severity"], "source": e["source"],
        "ts": e["ts"], "age_minutes": _age(e["ts"], now),
    } for e in top]
    return out
