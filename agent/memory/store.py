"""Agent memory: decisions, trade reviews and research notes in SQLite.

Memory helps the agent review past trades. It never feeds back into
strategy parameters automatically — the agent reads it explicitly.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    mode TEXT NOT NULL,
    market_context TEXT,
    analysis TEXT,
    tool_name TEXT,
    tool_arguments TEXT,
    outcome TEXT
);
CREATE TABLE IF NOT EXISTS trade_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    subject TEXT NOT NULL,
    review TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    topic TEXT NOT NULL,
    note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    mode TEXT NOT NULL,
    symbol TEXT,
    regime TEXT,
    trend TEXT,
    volatility TEXT,
    action TEXT,
    strategy TEXT,
    confidence REAL,
    evidence TEXT,
    raw TEXT
);
"""


class MemoryStore:
    def __init__(self, db_path: str = "data/agent_memory.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)

    def record_decision(
        self,
        mode: str,
        market_context: str,
        analysis: str,
        tool_name: str | None = None,
        tool_arguments: dict[str, Any] | None = None,
        outcome: str | None = None,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO decisions (ts, mode, market_context, analysis, tool_name, tool_arguments, outcome)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                mode,
                market_context,
                analysis,
                tool_name,
                json.dumps(tool_arguments or {}),
                outcome,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def update_outcome(self, decision_id: int, outcome: str) -> None:
        self._conn.execute(
            "UPDATE decisions SET outcome = ? WHERE id = ?", (outcome, decision_id)
        )
        self._conn.commit()

    def record_review(self, subject: str, review: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO trade_reviews (ts, subject, review) VALUES (?, ?, ?)",
            (time.time(), subject, review),
        )
        self._conn.commit()
        return cur.lastrowid

    def record_note(self, topic: str, note: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO research_notes (ts, topic, note) VALUES (?, ?, ?)",
            (time.time(), topic, note),
        )
        self._conn.commit()
        return cur.lastrowid

    def record_analysis(
        self,
        mode: str,
        symbol: str | None = None,
        regime: str | None = None,
        trend: str | None = None,
        volatility: str | None = None,
        action: str | None = None,
        strategy: str | None = None,
        confidence: float | None = None,
        evidence: str | None = None,
        raw: str | None = None,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO market_analysis"
            " (ts, mode, symbol, regime, trend, volatility, action, strategy,"
            " confidence, evidence, raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(), mode, symbol, regime, trend, volatility,
                action, strategy, confidence, evidence, raw,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def latest_analysis(self, symbol: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        if symbol:
            rows = self._conn.execute(
                "SELECT id, ts, mode, symbol, regime, trend, volatility, action,"
                " strategy, confidence, evidence FROM market_analysis"
                " WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, ts, mode, symbol, regime, trend, volatility, action,"
                " strategy, confidence, evidence FROM market_analysis"
                " ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        keys = ["id", "ts", "mode", "symbol", "regime", "trend", "volatility",
                "action", "strategy", "confidence", "evidence"]
        return [dict(zip(keys, r)) for r in rows]

    def recent_decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, ts, mode, market_context, analysis, tool_name, tool_arguments, outcome"
            " FROM decisions ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "ts": r[1],
                "mode": r[2],
                "market_context": r[3],
                "analysis": r[4],
                "tool_name": r[5],
                "tool_arguments": json.loads(r[6] or "{}"),
                "outcome": r[7],
            }
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()
