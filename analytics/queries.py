"""Read-side queries over analytics.db for the dashboard and reports.

Pure functions taking a db_path — callers open short-lived connections so
the analytics file is never locked for long, and a failure here can never
affect the trading path.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def _rows(db_path: str, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _row(db_path: str, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    rows = _rows(db_path, sql, params)
    return rows[0] if rows else None


def list_decision_events(
    db_path: str,
    symbol: str | None = None,
    action: str | None = None,
    regime: str | None = None,
    experiment_id: str | None = None,
    execution_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT decision_id, ts, mode, symbol, market_regime, action, strategy,"
        " confidence, evidence, risk_status, outcome_status, execution_id,"
        " agent_version, prompt_version, config_version, experiment_id"
        " FROM decision_events WHERE 1=1"
    )
    params: list[Any] = []
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol.upper())
    if action:
        sql += " AND action = ?"
        params.append(action.upper())
    if regime:
        sql += " AND market_regime = ?"
        params.append(regime.upper())
    if experiment_id:
        sql += " AND experiment_id = ?"
        params.append(experiment_id)
    if execution_id:
        sql += " AND execution_id = ?"
        params.append(execution_id)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    return _rows(db_path, sql, tuple(params))


def get_decision_event(db_path: str, decision_id: str) -> dict[str, Any] | None:
    row = _row(db_path, "SELECT * FROM decision_events WHERE decision_id = ?",
               (decision_id,))
    if row:
        for key in ("market_context", "portfolio_context", "tool_arguments"):
            try:
                row[key] = json.loads(row.get(key) or "{}")
            except json.JSONDecodeError:
                row[key] = {}
    return row


def get_trade_event(db_path: str, executor_id: str) -> dict[str, Any] | None:
    return _row(db_path, "SELECT * FROM trade_events WHERE executor_id = ?",
                (executor_id,))


def list_trade_events(
    db_path: str,
    strategy: str | None = None,
    regime: str | None = None,
    source: str | None = None,
    experiment_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM trade_events WHERE 1=1"
    params: list[Any] = []
    if strategy:
        sql += " AND strategy = ?"
        params.append(strategy)
    if regime:
        sql += " AND regime_at_entry = ?"
        params.append(regime.upper())
    if source:
        sql += " AND source = ?"
        params.append(source)
    if experiment_id:
        sql += " AND experiment_id = ?"
        params.append(experiment_id)
    sql += " ORDER BY ts_open DESC LIMIT ?"
    params.append(limit)
    return _rows(db_path, sql, tuple(params))


def list_review_events(
    db_path: str, execution_id: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    if execution_id:
        return _rows(
            db_path,
            "SELECT * FROM review_events WHERE execution_id = ?"
            " ORDER BY ts DESC LIMIT ?",
            (execution_id, limit),
        )
    return _rows(db_path, "SELECT * FROM review_events ORDER BY ts DESC LIMIT ?",
                 (limit,))


def decision_distribution(db_path: str, experiment_id: str | None = None) -> dict[str, int]:
    sql = "SELECT action, COUNT(*) c FROM decision_events"
    params: tuple = ()
    if experiment_id:
        sql += " WHERE experiment_id = ?"
        params = (experiment_id,)
    sql += " GROUP BY action"
    return {r["action"] or "UNKNOWN": r["c"] for r in _rows(db_path, sql, params)}


def attribution(
    db_path: str,
    group_by: str,
    experiment_id: str | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """PnL / win rate / trade count grouped by strategy or regime_at_entry."""
    if group_by not in ("strategy", "regime_at_entry"):
        raise ValueError(f"unsupported group_by {group_by!r}")
    sql = f"""
        SELECT COALESCE({group_by}, 'UNKNOWN') AS bucket,
               COUNT(*) AS trade_count,
               SUM(CASE WHEN ts_close IS NOT NULL THEN 1 ELSE 0 END) AS closed_count,
               ROUND(SUM(COALESCE(pnl_quote, 0)), 8) AS total_pnl_quote,
               ROUND(SUM(COALESCE(fees_quote, 0)), 8) AS total_fees_quote,
               AVG(CASE WHEN ts_close IS NOT NULL AND pnl_quote IS NOT NULL
                        THEN CASE WHEN pnl_quote > 0 THEN 1.0 ELSE 0.0 END END
                   ) AS win_rate
        FROM trade_events
    """
    conds: list[str] = []
    params: list[Any] = []
    if experiment_id:
        conds.append("experiment_id = ?")
        params.append(experiment_id)
    if source:
        conds.append("source = ?")
        params.append(source)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += f" GROUP BY bucket ORDER BY total_pnl_quote DESC"
    return _rows(db_path, sql, tuple(params))


def list_experiments(db_path: str) -> list[dict[str, Any]]:
    return _rows(db_path, "SELECT * FROM experiments ORDER BY started_at DESC")
