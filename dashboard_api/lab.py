"""AI Quant Lab API: decision events, replay, trade attribution.

Read-only against Hummingbot; writes only to analytics.db (the analytics
path — a failure here can never affect trading). Chain-of-thought is never
exposed: decision events carry structured summaries and evidence only.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from analytics import queries
from analytics.sync import sync_trade_events
from dashboard_api.hummingbot import ReadOnlyHummingbot

REPO_ROOT = Path(__file__).resolve().parent.parent
ANALYTICS_DB = str(REPO_ROOT / "data" / "analytics.db")

router = APIRouter(prefix="/api/lab", tags=["lab"])


def _analytics_available() -> None:
    if not Path(ANALYTICS_DB).exists():
        raise HTTPException(
            status_code=503,
            detail="analytics.db not found — no agent decision events recorded yet",
        )


@router.get("/decisions")
async def list_decisions(
    symbol: str | None = None,
    action: str | None = None,
    regime: str | None = None,
    experiment_id: str | None = None,
    execution_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    _analytics_available()
    limit = max(1, min(limit, 500))
    return {"data": queries.list_decision_events(
        ANALYTICS_DB, symbol=symbol, action=action, regime=regime,
        experiment_id=experiment_id, execution_id=execution_id, limit=limit,
    )}


@router.get("/decisions/{decision_id}")
async def decision_replay(decision_id: str) -> dict[str, Any]:
    """Full replay bundle: decision -> market/portfolio context -> execution
    -> outcome -> review. Everything needed to reconstruct one decision."""
    _analytics_available()
    event = queries.get_decision_event(ANALYTICS_DB, decision_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"decision {decision_id!r} not found")

    trade = None
    reviews: list[dict[str, Any]] = []
    execution_id = event.get("execution_id")
    if execution_id:
        trade = queries.get_trade_event(ANALYTICS_DB, execution_id)
        reviews = queries.list_review_events(ANALYTICS_DB, execution_id=execution_id)
    return {"data": {"decision": event, "trade": trade, "reviews": reviews}}


@router.get("/trades")
async def list_trades(
    strategy: str | None = None,
    regime: str | None = None,
    source: str | None = None,
    experiment_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Trade events, synced from Hummingbot executors on each call."""
    _analytics_available()
    hb = ReadOnlyHummingbot()
    try:
        result = await hb.call("POST", "/executors/search", json={"limit": 500})
        executors = result.get("data", []) if isinstance(result, dict) else []
        synced = sync_trade_events(ANALYTICS_DB, executors)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Hummingbot sync error: {exc}") from exc
    limit = max(1, min(limit, 500))
    return {
        "synced": synced,
        "data": queries.list_trade_events(
            ANALYTICS_DB, strategy=strategy, regime=regime, source=source,
            experiment_id=experiment_id, limit=limit,
        ),
    }


@router.get("/decision-distribution")
async def decision_distribution(experiment_id: str | None = None) -> dict[str, Any]:
    _analytics_available()
    return {"data": queries.decision_distribution(ANALYTICS_DB, experiment_id)}


@router.get("/attribution/{group_by}")
async def get_attribution(
    group_by: str, experiment_id: str | None = None
) -> dict[str, Any]:
    """PnL attribution by 'strategy' or 'regime' (regime_at_entry)."""
    _analytics_available()
    column = {"strategy": "strategy", "regime": "regime_at_entry"}.get(group_by)
    if not column:
        raise HTTPException(status_code=400, detail="group_by must be 'strategy' or 'regime'")
    return {"data": queries.attribution(ANALYTICS_DB, column, experiment_id)}


@router.get("/metrics")
async def get_metrics(experiment_id: str | None = None) -> dict[str, Any]:
    """Combined performance report: trading + equity + execution + exposure.

    Sources: analytics.db trade_events, dashboard.db equity snapshots,
    Hummingbot orders/positions (read-only). Syncs trade events first so
    the numbers reflect current Hummingbot state.
    """
    _analytics_available()
    from analytics import metrics as m

    hb = ReadOnlyHummingbot()
    try:
        executors_result, orders_result, positions_result, portfolio = (
            await hb.call("POST", "/executors/search", json={"limit": 500}),
            await hb.call("POST", "/trading/orders/search", json={"limit": 500}),
            await hb.call("POST", "/trading/positions", json={}),
            await hb.call("POST", "/portfolio/state", json={}),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Hummingbot API error: {exc}") from exc

    executors = executors_result.get("data", []) if isinstance(executors_result, dict) else []
    sync_trade_events(ANALYTICS_DB, executors)
    trades = queries.list_trade_events(ANALYTICS_DB, experiment_id=experiment_id, limit=500)

    dash_db = REPO_ROOT / "data" / "dashboard.db"
    snapshots: list[dict[str, Any]] = []
    if dash_db.exists():
        conn = sqlite3.connect(dash_db)
        rows = conn.execute(
            "SELECT ts, equity FROM equity_snapshots ORDER BY ts LIMIT 5000"
        ).fetchall()
        conn.close()
        snapshots = [{"ts": ts, "equity": eq} for ts, eq in rows]

    equity = 0.0
    for account, connectors in (portfolio or {}).items():
        for connector, tokens in (connectors or {}).items():
            for token in tokens or []:
                equity += float(token.get("value") or 0)

    positions = positions_result.get("data", []) if isinstance(positions_result, dict) else []
    orders = orders_result.get("data", []) if isinstance(orders_result, dict) else []

    return {
        "data": {
            "trading": m.trading_metrics(trades),
            "equity": m.equity_metrics(snapshots),
            "execution": m.execution_metrics(orders),
            "exposure": m.exposure_metrics(positions, equity),
            "decisions": queries.decision_distribution(ANALYTICS_DB, experiment_id),
            "attribution": {
                "strategy": queries.attribution(ANALYTICS_DB, "strategy", experiment_id),
                "regime": queries.attribution(ANALYTICS_DB, "regime_at_entry", experiment_id),
            },
        }
    }


@router.get("/experiments")
async def list_experiments() -> dict[str, Any]:
    _analytics_available()
    return {"data": queries.list_experiments(ANALYTICS_DB)}


@router.get("/compare")
async def compare_ai_vs_baseline(experiment_id: str | None = None) -> dict[str, Any]:
    """AI-assisted vs baseline vs manual: trading metrics per source."""
    _analytics_available()
    from analytics import metrics as m

    trades = queries.list_trade_events(ANALYTICS_DB, experiment_id=experiment_id, limit=1000)
    by_source: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        by_source.setdefault(t.get("source") or "unknown", []).append(t)
    return {"data": {src: m.trading_metrics(ts) for src, ts in by_source.items()}}


@router.get("/report/{experiment_id}")
async def experiment_report(experiment_id: str) -> dict[str, Any]:
    _analytics_available()
    from analytics.report import generate_report

    return {"data": generate_report(ANALYTICS_DB, experiment_id)}


def analytics_db_ok() -> dict[str, Any]:
    """Health-check helper for /api/health — real probe, no fake green."""
    try:
        conn = sqlite3.connect(ANALYTICS_DB)
        n = conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
        conn.close()
        return {"ok": True, "detail": f"{n} decision events"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
