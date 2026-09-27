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
