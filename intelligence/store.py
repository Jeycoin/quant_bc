"""Intelligence store: normalized signals / news / narratives.

Separate SQLite DB from analytics.db (analytics path separation): if
intelligence ingestion dies, trading and trading analytics are untouched.
Writes are additive and timestamped — history is kept so future replays
can reconstruct what the agent could have known at time T.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from intelligence.schema import IntelSignal, Narrative, NewsEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intel_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    symbol TEXT,
    source TEXT NOT NULL,
    value REAL,
    confidence REAL,
    relevance REAL,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_intel_signals_ts ON intel_signals(ts);
CREATE INDEX IF NOT EXISTS idx_intel_signals_kind ON intel_signals(kind, symbol);

CREATE TABLE IF NOT EXISTS news_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source TEXT NOT NULL,
    title TEXT,
    summary TEXT,
    asset TEXT,
    category TEXT,
    severity REAL,
    relevance REAL,
    url TEXT,
    dedupe_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_news_events_ts ON news_events(ts);
CREATE INDEX IF NOT EXISTS idx_news_events_asset ON news_events(asset);

CREATE TABLE IF NOT EXISTS narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    symbol TEXT,
    name TEXT NOT NULL,
    strength REAL,
    novelty REAL,
    social_momentum REAL,
    price_confirmation REAL,
    onchain_confirmation REAL,
    confidence REAL,
    evidence TEXT
);
CREATE INDEX IF NOT EXISTS idx_narratives_ts ON narratives(ts);
"""


class IntelligenceStore:
    def __init__(self, db_path: str = "data/intelligence.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)

    # -------------------------------------------------------------- signals

    def record_signal(self, s: IntelSignal) -> None:
        self._conn.execute(
            "INSERT INTO intel_signals"
            " (ts, kind, symbol, source, value, confidence, relevance, payload)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (s.ts, s.kind, s.symbol, s.source, s.value, s.confidence,
             s.relevance, json.dumps(s.payload, ensure_ascii=False)),
        )
        self._conn.commit()

    def record_signals(self, signals: list[IntelSignal]) -> None:
        self._conn.executemany(
            "INSERT INTO intel_signals"
            " (ts, kind, symbol, source, value, confidence, relevance, payload)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(s.ts, s.kind, s.symbol, s.source, s.value, s.confidence,
              s.relevance, json.dumps(s.payload, ensure_ascii=False))
             for s in signals],
        )
        self._conn.commit()

    def recent_signals(self, kind: str | None = None, symbol: str | None = None,
                       hours: float = 24) -> list[dict[str, Any]]:
        q = "SELECT * FROM intel_signals WHERE ts >= ?"
        args: list[Any] = [time.time() - hours * 3600]
        if kind:
            q += " AND kind = ?"
            args.append(kind)
        if symbol:
            q += " AND symbol = ?"
            args.append(symbol)
        q += " ORDER BY ts DESC"
        self._conn.row_factory = sqlite3.Row
        rows = self._conn.execute(q, args).fetchall()
        self._conn.row_factory = None
        return [dict(r) for r in rows]

    # ----------------------------------------------------------------- news

    def record_news(self, n: NewsEvent) -> bool:
        """Insert a news event; dedupe on (source, title) so provider
        polling can overlap without duplicates. Returns True if inserted."""
        key = f"{n.source}:{n.title[:120]}"
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO news_events"
            " (ts, source, title, summary, asset, category, severity,"
            " relevance, url, dedupe_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (n.ts, n.source, n.title, n.summary, n.asset, n.category,
             n.severity, n.relevance, n.url, key),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def recent_news(self, asset: str | None = None, hours: float = 24,
                    limit: int = 50) -> list[dict[str, Any]]:
        q = "SELECT * FROM news_events WHERE ts >= ?"
        args: list[Any] = [time.time() - hours * 3600]
        if asset:
            q += " AND (asset = ? OR asset IS NULL)"
            args.append(asset)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        self._conn.row_factory = sqlite3.Row
        rows = self._conn.execute(q, args).fetchall()
        self._conn.row_factory = None
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ narratives

    def record_narrative(self, n: Narrative) -> None:
        self._conn.execute(
            "INSERT INTO narratives"
            " (ts, symbol, name, strength, novelty, social_momentum,"
            " price_confirmation, onchain_confirmation, confidence, evidence)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (n.ts, n.symbol, n.name, n.strength, n.novelty, n.social_momentum,
             n.price_confirmation, n.onchain_confirmation, n.confidence,
             json.dumps(n.evidence, ensure_ascii=False)),
        )
        self._conn.commit()

    def recent_narratives(self, symbol: str | None = None, hours: float = 24,
                          limit: int = 20) -> list[dict[str, Any]]:
        q = "SELECT * FROM narratives WHERE ts >= ?"
        args: list[Any] = [time.time() - hours * 3600]
        if symbol:
            q += " AND (symbol = ? OR symbol IS NULL)"
            args.append(symbol)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        self._conn.row_factory = sqlite3.Row
        rows = self._conn.execute(q, args).fetchall()
        self._conn.row_factory = None
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
