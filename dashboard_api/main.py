"""Dashboard backend: read-only aggregation over the existing system.

Data sources (nothing is recomputed here):
  - Hummingbot API (balances, positions, orders, executors, market data)
  - Agent memory SQLite (decisions, reviews, notes)

Trading logic lives in the agent / Hummingbot. This service only reads.
All endpoints are shaped so short polling can later be swapped for SSE
behind the same URLs.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import asyncio
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from dashboard_api.hummingbot import EndpointNotAllowed, ReadOnlyHummingbot

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

MEMORY_DB = REPO_ROOT / "data" / "agent_memory.db"
MARKET_PROBE_CONNECTOR = "hyperliquid_perpetual"
MARKET_PROBE_PAIR = "BTC-USD"

app = FastAPI(title="AI Quant Dashboard API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

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

    return {"mode": trading_mode(), "checks": checks,
            "ok": all(c["ok"] for c in checks.values())}


@app.get("/api/overview")
async def get_overview() -> dict[str, Any]:
    try:
        portfolio = await hb.call("POST", "/portfolio/state", json={})
        executors = await hb.call("GET", "/executors/summary")
        positions = await hb.call("POST", "/trading/positions", json={})
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
    return {
        "mode": trading_mode(),
        "equity": equity,
        "balances": balances,
        "executors": executors,
        "open_positions": position_rows,
        "open_position_count": len(position_rows),
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


MARKET_SYMBOLS = {"BTC": "BTC-USD", "ETH": "ETH-USD"}
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
        conn = sqlite3.connect(MEMORY_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, ts, mode, market_context, analysis, tool_name, tool_arguments, outcome"
            " FROM decisions ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"memory db error: {exc}") from exc
    return {"data": [dict(r) for r in rows]}
