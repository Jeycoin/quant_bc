"""Sync Hummingbot executor records into analytics trade_events.

Pure functions: the caller (dashboard API or a script) fetches executors
from Hummingbot and passes the dicts in. Attribution rules:

- source = 'agent' when a decision_events row references the executor id
  (the executor was created by the agent), otherwise 'manual'. Baseline
  runs are tagged by the experiment tooling, not here.
- regime_at_entry = the agent's most recent regime classification for the
  symbol's base asset at or before the executor's open time.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any


def _iso_to_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _base_asset(trading_pair: str | None) -> str | None:
    if not trading_pair:
        return None
    return trading_pair.partition("-")[0] or None


def sync_trade_events(db_path: str, executors: list[dict[str, Any]]) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    synced = 0
    try:
        for e in executors:
            executor_id = e.get("executor_id")
            if not executor_id:
                continue
            ts_open = _iso_to_ts(e.get("created_at"))
            ts_close = e.get("close_timestamp") or _iso_to_ts(e.get("closed_at"))
            base = _base_asset(e.get("trading_pair"))

            link = conn.execute(
                "SELECT decision_id FROM decision_events WHERE execution_id = ?"
                " ORDER BY ts DESC LIMIT 1",
                (executor_id,),
            ).fetchone()
            source = "agent" if link else "manual"
            decision_id = link["decision_id"] if link else None

            regime = None
            if base and ts_open:
                row = conn.execute(
                    "SELECT market_regime FROM decision_events"
                    " WHERE symbol = ? AND market_regime IS NOT NULL AND ts <= ?"
                    " ORDER BY ts DESC LIMIT 1",
                    (base, ts_open),
                ).fetchone()
                regime = row["market_regime"] if row else None

            existing = conn.execute(
                "SELECT source, decision_id FROM trade_events WHERE executor_id = ?",
                (executor_id,),
            ).fetchone()
            if existing and existing["source"] == "baseline":
                source = "baseline"  # never clobber an explicit baseline tag
                decision_id = existing["decision_id"]

            conn.execute(
                "INSERT OR REPLACE INTO trade_events"
                " (executor_id, decision_id, ts_open, ts_close, symbol, strategy,"
                " connector, side, status, close_type, pnl_quote, pnl_pct,"
                " filled_amount_quote, fees_quote, regime_at_entry, source,"
                " experiment_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " COALESCE((SELECT experiment_id FROM trade_events"
                "          WHERE executor_id = ?), NULL))",
                (
                    executor_id, decision_id, ts_open, ts_close,
                    e.get("trading_pair"), e.get("executor_type"),
                    e.get("connector_name"), e.get("side"), e.get("status"),
                    e.get("close_type"), e.get("net_pnl_quote"),
                    e.get("net_pnl_pct"), e.get("filled_amount_quote"),
                    e.get("cum_fees_quote"), regime, source, executor_id,
                ),
            )
            synced += 1
        conn.commit()
    finally:
        conn.close()
    return synced
