"""Dashboard intelligence endpoints (v0.4 Phase 7).

Read-only views over the intelligence layer + deterministic validators.
Nothing here places orders or mutates state — the dashboard visualizes
what the existing system already knows.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from fastapi import APIRouter, Query

from agent.validators import compute_grid_features, evaluate_grid_state
from dashboard_api.hummingbot import ReadOnlyHummingbot
from intelligence.snapshot import build_snapshot
from intelligence.store import IntelligenceStore

router = APIRouter(prefix="/api/intel", tags=["intel"])
hb = ReadOnlyHummingbot()

INTEL_DB = os.getenv("INTELLIGENCE_DB", "data/intelligence.db")
ANALYTICS_DB = os.getenv("ANALYTICS_DB", "data/analytics.db")
SYMBOLS = ["BTC", "ETH"]


def _intel_db_ok() -> bool:
    return os.path.exists(INTEL_DB)


@router.get("/overview")
async def intel_overview() -> dict[str, Any]:
    """Intelligence Overview: social / onchain / news / narratives +
    grid protection state per symbol."""
    if not _intel_db_ok():
        return {"available": False, "reason": "intelligence DB not found"}
    store = IntelligenceStore(INTEL_DB)
    try:
        # market context for narrative detection + grid protection features
        market: dict[str, dict[str, Any]] = {}
        grid_states: dict[str, Any] = {}
        for sym in SYMBOLS:
            try:
                data = await hb.call("POST", "/market-data/candles", json={
                    "connector_name": os.getenv("MARKET_PROBE_CONNECTOR",
                                                "hyperliquid_perpetual"),
                    "trading_pair": f"{sym}-USD",
                    "interval": "1h", "max_records": 120,
                })
                rows = data.get("candles", data) if isinstance(data, dict) else data
                candles = [{"high": float(c["high"]), "low": float(c["low"]),
                            "close": float(c["close"])} for c in (rows or [])]
                if len(candles) >= 25:
                    market[sym] = {"24h_change_pct": round(
                        (candles[-1]["close"] / candles[-25]["close"] - 1) * 100, 2)}
                state = evaluate_grid_state(compute_grid_features(candles))
                grid_states[sym] = {"state": state.state, "reasons": state.reasons}
            except Exception as exc:
                grid_states[sym] = {"state": "UNKNOWN", "reasons": [str(exc)[:120]]}
        snapshot = build_snapshot(store, SYMBOLS, market=market)
        out = snapshot.for_llm()
        out["available"] = True
        out["grid_protection"] = grid_states
        return out
    finally:
        store.close()


@router.get("/narratives")
def intel_narratives(symbol: str | None = Query(default=None),
                     hours: float = Query(default=24)) -> dict[str, Any]:
    if not _intel_db_ok():
        return {"data": []}
    store = IntelligenceStore(INTEL_DB)
    try:
        return {"data": store.recent_narratives(symbol=symbol, hours=hours)}
    finally:
        store.close()


@router.get("/news")
def intel_news(asset: str | None = Query(default=None),
               hours: float = Query(default=24)) -> dict[str, Any]:
    if not _intel_db_ok():
        return {"data": []}
    store = IntelligenceStore(INTEL_DB)
    try:
        return {"data": store.recent_news(asset=asset, hours=hours)}
    finally:
        store.close()


@router.get("/rejections")
def intel_rejections(hours: float = Query(default=168)) -> dict[str, Any]:
    """Rejected Opportunities: proposal counts by gate + recent rejections."""
    if not os.path.exists(ANALYTICS_DB):
        return {"summary": {}, "recent": []}
    import time

    conn = sqlite3.connect(ANALYTICS_DB)
    conn.row_factory = sqlite3.Row
    try:
        since = time.time() - hours * 3600
        summary: dict[str, int] = {}
        for row in conn.execute(
            "SELECT risk_status, COUNT(*) AS n FROM decision_events"
            " WHERE ts >= ? AND risk_status IS NOT NULL GROUP BY risk_status",
            (since,),
        ):
            summary[row["risk_status"]] = row["n"]
        recent = [dict(r) for r in conn.execute(
            "SELECT decision_id, ts, symbol, action, strategy, risk_status,"
            " rejection_reason, experiment_id FROM decision_events"
            " WHERE ts >= ? AND outcome_status = 'REJECTED'"
            " ORDER BY ts DESC LIMIT 30",
            (since,),
        )]
        return {"summary": summary, "recent": recent}
    finally:
        conn.close()
