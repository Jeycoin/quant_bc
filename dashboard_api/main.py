"""Dashboard backend: read-only aggregation over the existing system.

Data sources (nothing is recomputed here):
  - Hummingbot API (balances, positions, orders, executors, market data)
  - Agent memory SQLite (decisions, reviews, notes)

Trading logic lives in the agent / Hummingbot. This service only reads.
All endpoints are shaped so short polling can later be swapped for SSE
behind the same URLs.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from dashboard_api.events import analysis_to_event, decision_to_event, review_to_event
from dashboard_api.hummingbot import EndpointNotAllowed, ReadOnlyHummingbot
from dashboard_api.lab import analytics_db_ok
from dashboard_api.lab import router as lab_router
from dashboard_api.intel import router as intel_router

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

MEMORY_DB = REPO_ROOT / "data" / "agent_memory.db"
DASH_DB = REPO_ROOT / "data" / "dashboard.db"
MARKET_PROBE_CONNECTOR = "hyperliquid_perpetual"
MARKET_PROBE_PAIR = "BTC-USD"


def _init_dash_db() -> None:
    DASH_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DASH_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS equity_snapshots (ts REAL NOT NULL, equity REAL NOT NULL)"
    )
    conn.commit()
    conn.close()


_init_dash_db()

# Ensure the agent memory schema is current (creates missing tables only;
# MemoryStore is the single source of truth for the schema).
from agent.memory.store import MemoryStore

MemoryStore(str(MEMORY_DB)).close()


def _record_equity_snapshot(equity: float, min_interval_s: int = 60) -> None:
    conn = sqlite3.connect(DASH_DB)
    row = conn.execute("SELECT MAX(ts) FROM equity_snapshots").fetchone()
    now = time.time()
    if not row[0] or now - row[0] >= min_interval_s:
        conn.execute("INSERT INTO equity_snapshots (ts, equity) VALUES (?, ?)", (now, equity))
        conn.commit()
    conn.close()


def _equity_stats(current: float) -> dict[str, Any]:
    conn = sqlite3.connect(DASH_DB)
    rows = conn.execute(
        "SELECT ts, equity FROM equity_snapshots ORDER BY ts DESC LIMIT 500"
    ).fetchall()
    conn.close()
    history = [{"ts": ts, "equity": eq} for ts, eq in reversed(rows)]
    peak = max([current] + [p["equity"] for p in history])
    drawdown_pct = (peak - current) / peak * 100 if peak else 0.0
    return {"peak": peak, "drawdown_pct": drawdown_pct, "history": history}


def _memory_rows(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    conn = sqlite3.connect(MEMORY_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

app = FastAPI(title="AI Quant Dashboard API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.include_router(lab_router)
app.include_router(intel_router)

hb = ReadOnlyHummingbot()


def trading_mode() -> str:
    live = os.getenv("LIVE_TRADING", "false").strip().lower() == "true"
    return "LIVE" if live else "TESTNET"  # paper path runs on testnet connectors


@app.get("/api/mode")
async def get_mode() -> dict[str, Any]:
    live_env = os.getenv("LIVE_TRADING", "false").strip().lower() == "true"
    return {
        "mode": trading_mode(),
        "live_trading_env": live_env,
        "note": "Mode is set by LIVE_TRADING in .env + restart. The dashboard cannot change it.",
    }


@app.get("/api/health")
async def get_health() -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}

    try:
        await hb.call("GET", "/")
        checks["hummingbot"] = {"ok": True, "detail": "API reachable"}
    except Exception as exc:
        checks["hummingbot"] = {"ok": False, "detail": str(exc)}

    try:
        price = await hb.call(
            "POST",
            "/market-data/prices",
            json={"connector_name": MARKET_PROBE_CONNECTOR,
                  "trading_pairs": [MARKET_PROBE_PAIR]},
        )
        checks["exchange"] = {"ok": True, "detail": f"{MARKET_PROBE_CONNECTOR} {price}"}
    except Exception as exc:
        checks["exchange"] = {"ok": False, "detail": str(exc)}

    try:
        conn = sqlite3.connect(MEMORY_DB)
        decisions = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        conn.close()
        checks["database"] = {"ok": True, "detail": f"{decisions} decisions recorded"}
    except Exception as exc:
        checks["database"] = {"ok": False, "detail": str(exc)}

    mcp_cmd = os.getenv("MCP_HUMMINGBOT_COMMAND", "uvx")
    if shutil.which(mcp_cmd):
        checks["mcp"] = {"ok": True, "detail": f"on-demand stdio server ({mcp_cmd}) available"}
    else:
        checks["mcp"] = {"ok": False, "detail": f"{mcp_cmd} not found on PATH"}

    provider = os.getenv("LLM_PROVIDER", "anthropic")
    key = os.getenv("LLM_API_KEY") if provider == "openai_compat" else os.getenv("ANTHROPIC_API_KEY")
    model = os.getenv("LLM_MODEL") if provider == "openai_compat" else os.getenv("ANTHROPIC_MODEL")
    if key:
        checks["agent"] = {"ok": True, "detail": f"LLM configured ({provider}/{model}); agent runs on demand"}
    else:
        checks["agent"] = {"ok": False, "detail": f"no API key for provider {provider}"}

    checks["data"] = analytics_db_ok()

    try:
        from analytics.store import AnalyticsStore

        store = AnalyticsStore(str(REPO_ROOT / "data" / "analytics.db"))
        try:
            active = store.get_active_experiment()
        finally:
            store.close()
        if active:
            checks["experiment"] = {"ok": True, "detail": f"running: {active['name']}"}
        else:
            checks["experiment"] = {"ok": True, "detail": "no active experiment"}
    except Exception as exc:
        checks["experiment"] = {"ok": False, "detail": str(exc)}

    # intelligence layer: real check — DB reachable + freshest item age
    try:
        from intelligence.store import IntelligenceStore

        istore = IntelligenceStore(str(REPO_ROOT / "data" / "intelligence.db"))
        try:
            news = istore.recent_news(hours=720, limit=1)
            signals = istore.recent_signals(hours=720)[:1]
        finally:
            istore.close()
        latest = max([r["ts"] for r in (news + signals)], default=None)
        if latest is None:
            checks["intelligence"] = {"ok": False, "detail": "no data collected yet"}
        else:
            age_h = (time.time() - latest) / 3600
            checks["intelligence"] = {
                "ok": age_h < 2,
                "detail": f"freshest item {age_h:.1f}h old"
                          + (" (collector stale)" if age_h >= 2 else ""),
            }
    except Exception as exc:
        checks["intelligence"] = {"ok": False, "detail": str(exc)}

    return {"mode": trading_mode(), "checks": checks,
            "ok": all(c["ok"] for c in checks.values())}


@app.get("/api/overview")
async def get_overview() -> dict[str, Any]:
    try:
        portfolio, executors, positions = await asyncio.gather(
            hb.call("POST", "/portfolio/state", json={}),
            hb.call("GET", "/executors/summary"),
            hb.call("POST", "/trading/positions", json={}),
        )
    except EndpointNotAllowed as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Hummingbot API error: {exc}") from exc

    balances = []
    equity = 0.0
    for account, connectors in (portfolio or {}).items():
        for connector, tokens in (connectors or {}).items():
            for token in tokens or []:
                value = float(token.get("value") or 0)
                equity += value
                balances.append({
                    "account": account,
                    "connector": connector,
                    "token": token.get("token"),
                    "units": token.get("units"),
                    "available_units": token.get("available_units"),
                    "price": token.get("price"),
                    "value": value,
                })

    position_rows = positions.get("data", []) if isinstance(positions, dict) else []
    _record_equity_snapshot(equity)
    stats = _equity_stats(equity)
    return {
        "mode": trading_mode(),
        "equity": equity,
        "balances": balances,
        "executors": executors,
        "open_positions": position_rows,
        "open_position_count": len(position_rows),
        "equity_history": stats["history"],
        "peak_equity": stats["peak"],
        "drawdown_pct": stats["drawdown_pct"],
    }


async def _hb_call(method: str, path: str, **kwargs: Any) -> Any:
    """Shared error mapping for read-only Hummingbot calls."""
    try:
        return await hb.call(method, path, **kwargs)
    except EndpointNotAllowed as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Hummingbot API error: {exc}") from exc


@app.get("/api/orders")
async def get_orders(
    status: str | None = None,
    trading_pair: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"limit": max(1, min(limit, 500))}
    if status:
        payload["status"] = status
    if trading_pair:
        payload["trading_pairs"] = [trading_pair]
    return await _hb_call("POST", "/trading/orders/search", json=payload)


@app.get("/api/orders/active")
async def get_active_orders() -> dict[str, Any]:
    return await _hb_call("POST", "/trading/orders/active", json={})


@app.get("/api/positions")
async def get_positions() -> dict[str, Any]:
    positions = await _hb_call("POST", "/trading/positions", json={})
    rows = positions.get("data", []) if isinstance(positions, dict) else []

    # Indicative mark prices from the live market-data connector (execution
    # happens on testnet connectors, which expose no market data feed).
    marks: dict[str, float | None] = {}
    for row in rows:
        base = str(row.get("trading_pair", "")).partition("-")[0]
        if not base or base in marks:
            continue
        try:
            res = await hb.call(
                "POST",
                "/market-data/prices",
                json={"connector_name": MARKET_PROBE_CONNECTOR,
                      "trading_pairs": [f"{base}-USD"]},
            )
            marks[base] = (res.get("prices") or {}).get(f"{base}-USD")
        except Exception:
            marks[base] = None

    for row in rows:
        base = str(row.get("trading_pair", "")).partition("-")[0]
        row["mark_price"] = marks.get(base)
        entry = row.get("entry_price") or 0
        mark = row.get("mark_price")
        row["mark_change_pct"] = (
            (mark - entry) / entry * 100 if mark and entry else None
        )
    return {"data": rows}


@app.get("/api/executors")
async def get_executors(status: str | None = None, limit: int = 100) -> dict[str, Any]:
    payload: dict[str, Any] = {"limit": max(1, min(limit, 500))}
    if status:
        payload["status"] = status
    return await _hb_call("POST", "/executors/search", json=payload)


def _grid_info(config: dict[str, Any]) -> dict[str, Any] | None:
    """Derive indicative grid levels from a grid executor config.

    Hummingbot spaces grid levels geometrically by the per-level
    take_profit step. Live per-level state only exists on a running
    executor; this is the configured (indicative) layout.
    """
    start = config.get("start_price")
    end = config.get("end_price")
    if not start or not end:
        return None
    tp = (config.get("triple_barrier_config") or {}).get("take_profit") or 0.0002
    levels: list[float] = []
    price = float(start)
    while price <= float(end) and len(levels) < 500:
        levels.append(round(price, 8))
        price *= 1 + float(tp)
    return {
        "start_price": start,
        "end_price": end,
        "limit_price": config.get("limit_price"),
        "total_amount_quote": config.get("total_amount_quote"),
        "take_profit_per_level": tp,
        "level_count": len(levels),
        "level_prices": levels[:100],
        "indicative": True,
    }


@app.get("/api/executors/{executor_id}")
async def get_executor_detail(executor_id: str) -> dict[str, Any]:
    detail = await _hb_call(
        "GET", "/executors/{executor_id}", path_params={"executor_id": executor_id}
    )
    if isinstance(detail, dict) and detail.get("executor_type") == "grid_executor":
        detail["grid_info"] = _grid_info(detail.get("config") or {})
    return detail


MARKET_SYMBOLS = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
                  "XRP": "XRP-USD", "SUI": "SUI-USD"}
MARKET_INTERVALS = {"1m", "5m", "15m", "1h"}


@app.get("/api/market")
async def get_market(symbol: str = "BTC", interval: str = "1m", limit: int = 200) -> dict[str, Any]:
    symbol = symbol.upper()
    if symbol not in MARKET_SYMBOLS:
        raise HTTPException(status_code=400, detail=f"unsupported symbol {symbol!r}")
    if interval not in MARKET_INTERVALS:
        raise HTTPException(status_code=400, detail=f"unsupported interval {interval!r}")
    pair = MARKET_SYMBOLS[symbol]
    limit = max(10, min(limit, 500))

    candles, funding, book = await asyncio.gather(
        _hb_call("POST", "/market-data/candles", json={
            "connector_name": MARKET_PROBE_CONNECTOR, "trading_pair": pair,
            "interval": interval, "max_records": limit}),
        _hb_call("POST", "/market-data/funding-info", json={
            "connector_name": MARKET_PROBE_CONNECTOR, "trading_pair": pair}),
        _hb_call("POST", "/market-data/order-book", json={
            "connector_name": MARKET_PROBE_CONNECTOR, "trading_pair": pair, "depth": 10}),
    )

    bids = book.get("bids") or []
    asks = book.get("asks") or []
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    spread = (best_ask - best_bid) if best_bid and best_ask else None
    last_price = candles[-1]["close"] if candles else None

    return {
        "symbol": symbol,
        "pair": pair,
        "connector": MARKET_PROBE_CONNECTOR,
        "interval": interval,
        "last_price": last_price,
        "candles": candles,
        "funding": funding,
        "order_book": {
            "bids": bids,
            "asks": asks,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "spread_pct": (spread / best_bid * 100) if spread and best_bid else None,
        },
        "open_interest": None,  # Hummingbot does not expose OI; phase 2+ external data
    }


@app.get("/api/memory/decisions")
async def get_decisions(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    try:
        rows = _memory_rows(
            "SELECT id, ts, mode, market_context, analysis, tool_name, tool_arguments, outcome"
            " FROM decisions ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"memory db error: {exc}") from exc
    return {"data": rows}


@app.get("/api/memory/reviews")
async def get_reviews(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    return {"data": _memory_rows(
        "SELECT id, ts, subject, review FROM trade_reviews ORDER BY id DESC LIMIT ?",
        (limit,),
    )}


@app.get("/api/memory/notes")
async def get_notes(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    return {"data": _memory_rows(
        "SELECT id, ts, topic, note FROM research_notes ORDER BY id DESC LIMIT ?",
        (limit,),
    )}


@app.get("/api/ai/analysis")
async def get_ai_analysis(symbol: str | None = None, limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    if symbol:
        rows = _memory_rows(
            "SELECT id, ts, mode, symbol, regime, trend, volatility, action, strategy,"
            " confidence, evidence FROM market_analysis WHERE symbol = ?"
            " ORDER BY id DESC LIMIT ?",
            (symbol.upper(), limit),
        )
    else:
        rows = _memory_rows(
            "SELECT id, ts, mode, symbol, regime, trend, volatility, action, strategy,"
            " confidence, evidence FROM market_analysis ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    return {"data": rows}


@app.get("/api/ai/timeline")
async def get_ai_timeline(limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(limit, 300))
    decisions = _memory_rows(
        "SELECT id, ts, mode, tool_name, tool_arguments, outcome"
        " FROM decisions ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    analyses = _memory_rows(
        "SELECT id, ts, mode, symbol, regime, action, confidence"
        " FROM market_analysis ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    reviews = _memory_rows(
        "SELECT id, ts, subject FROM trade_reviews ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    events = (
        [decision_to_event(r) for r in decisions]
        + [analysis_to_event(r) for r in analyses]
        + [review_to_event(r) for r in reviews]
    )
    events.sort(key=lambda e: e["ts"], reverse=True)
    return {"data": events[:limit]}


@app.get("/api/journal")
async def get_journal(limit: int = 100) -> dict[str, Any]:
    """Closed executors joined with trade reviews — the trade journal."""
    result = await _hb_call("POST", "/executors/search", json={"limit": max(1, min(limit, 500))})
    executors = result.get("data", []) if isinstance(result, dict) else []
    reviews = _memory_rows("SELECT id, ts, subject, review FROM trade_reviews")

    entries = []
    for e in executors:
        if not e.get("closed_at") and not e.get("close_timestamp"):
            continue  # still running — journal is for completed trades
        eid = e.get("executor_id", "")
        review = next((r for r in reviews if eid in (r.get("subject") or "")), None)
        created = e.get("created_at")
        closed_ts = e.get("close_timestamp")
        created_ts = None
        if created:
            from datetime import datetime
            created_ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
        entries.append({
            "executor_id": eid,
            "symbol": e.get("trading_pair"),
            "strategy": e.get("executor_type"),
            "connector": e.get("connector_name"),
            "opened_at": created,
            "closed_at": e.get("closed_at"),
            "duration_s": (closed_ts - created_ts) if closed_ts and created_ts else None,
            "pnl_quote": e.get("net_pnl_quote"),
            "pnl_pct": e.get("net_pnl_pct"),
            "filled_amount_quote": e.get("filled_amount_quote"),
            "fees_quote": e.get("cum_fees_quote"),
            "close_type": e.get("close_type"),
            "review": review.get("review") if review else None,
            "memory_id": review.get("id") if review else None,
        })
    return {"data": entries}


@app.get("/api/stream")
async def stream_prices() -> StreamingResponse:
    """SSE: watchlist prices + funding every 5s. Pages still poll as fallback;
    this endpoint is the seam for a future WebSocket upgrade."""

    async def generate():
        while True:
            payload: dict[str, Any] = {"ts": time.time()}
            try:
                res = await hb.call("POST", "/market-data/prices", json={
                    "connector_name": MARKET_PROBE_CONNECTOR,
                    "trading_pairs": list(MARKET_SYMBOLS.values()),
                })
                payload["prices"] = res.get("prices", {})
            except Exception as exc:
                payload["error"] = str(exc)
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
