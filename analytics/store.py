"""Analytics event store: decision / trade / review / experiment events.

Schema follows the Decision Replay requirement: queryable columns for
symbol, regime, action, strategy, versions and experiment; JSON snapshots
(market_context, portfolio_context) kept as TEXT blobs but never the only
copy of a fact that needs to be filtered on.

Chain-of-thought is never stored: only structured summaries, evidence
strings and tool arguments.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_events (
    decision_id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    mode TEXT NOT NULL,
    symbol TEXT,
    market_regime TEXT,
    action TEXT,
    strategy TEXT,
    confidence REAL,
    evidence TEXT,
    risk_status TEXT,
    tool_name TEXT,
    tool_arguments TEXT,
    outcome_status TEXT,
    execution_id TEXT,
    market_context TEXT,
    portfolio_context TEXT,
    agent_version TEXT,
    prompt_version TEXT,
    config_version TEXT,
    experiment_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_decision_events_ts ON decision_events(ts);
CREATE INDEX IF NOT EXISTS idx_decision_events_symbol ON decision_events(symbol);
CREATE INDEX IF NOT EXISTS idx_decision_events_regime ON decision_events(market_regime);
CREATE INDEX IF NOT EXISTS idx_decision_events_experiment ON decision_events(experiment_id);

CREATE TABLE IF NOT EXISTS trade_events (
    executor_id TEXT PRIMARY KEY,
    decision_id TEXT,
    ts_open REAL,
    ts_close REAL,
    symbol TEXT,
    strategy TEXT,
    connector TEXT,
    side TEXT,
    status TEXT,
    close_type TEXT,
    pnl_quote REAL,
    pnl_pct REAL,
    filled_amount_quote REAL,
    fees_quote REAL,
    regime_at_entry TEXT,
    source TEXT,
    experiment_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_trade_events_strategy ON trade_events(strategy);
CREATE INDEX IF NOT EXISTS idx_trade_events_regime ON trade_events(regime_at_entry);
CREATE INDEX IF NOT EXISTS idx_trade_events_experiment ON trade_events(experiment_id);

CREATE TABLE IF NOT EXISTS review_events (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    decision_id TEXT,
    execution_id TEXT,
    outcome TEXT,
    decision_quality TEXT,
    execution_quality TEXT,
    regime_accuracy TEXT,
    main_error TEXT,
    main_success TEXT,
    lesson TEXT,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_events_execution ON review_events(execution_id);

CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at REAL NOT NULL,
    ended_at REAL,
    symbols TEXT,
    strategies TEXT,
    agent_version TEXT,
    prompt_version TEXT,
    config_version TEXT,
    config_snapshot TEXT,
    notes TEXT
);
"""


def new_decision_id() -> str:
    return uuid.uuid4().hex[:16]


class AnalyticsStore:
    def __init__(self, db_path: str = "data/analytics.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Additive-only migrations. Existing experiments stay untouched;
        older rows simply have NULL in the new columns."""
        trade_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(trade_events)")}
        for col in ("gross_pnl", "entry_fee", "exit_fee", "slippage_quote"):
            if col not in trade_cols:
                self._conn.execute(f"ALTER TABLE trade_events ADD COLUMN {col} REAL")
        decision_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(decision_events)")}
        if "rejection_reason" not in decision_cols:
            self._conn.execute("ALTER TABLE decision_events ADD COLUMN rejection_reason TEXT")
        if "llm_model" not in decision_cols:
            self._conn.execute("ALTER TABLE decision_events ADD COLUMN llm_model TEXT")
        self._conn.commit()

    # ------------------------------------------------------------- decisions

    def record_decision_event(
        self,
        mode: str,
        decision_id: str | None = None,
        symbol: str | None = None,
        market_regime: str | None = None,
        action: str | None = None,
        strategy: str | None = None,
        confidence: float | None = None,
        evidence: str | None = None,
        risk_status: str | None = None,
        tool_name: str | None = None,
        tool_arguments: dict[str, Any] | None = None,
        outcome_status: str | None = None,
        execution_id: str | None = None,
        market_context: dict[str, Any] | None = None,
        portfolio_context: dict[str, Any] | None = None,
        agent_version: str | None = None,
        prompt_version: str | None = None,
        config_version: str | None = None,
        experiment_id: str | None = None,
        rejection_reason: str | None = None,
        llm_model: str | None = None,
        ts: float | None = None,
    ) -> str:
        did = decision_id or new_decision_id()
        self._conn.execute(
            "INSERT OR REPLACE INTO decision_events"
            " (decision_id, ts, mode, symbol, market_regime, action, strategy,"
            " confidence, evidence, risk_status, tool_name, tool_arguments,"
            " outcome_status, execution_id, market_context, portfolio_context,"
            " agent_version, prompt_version, config_version, experiment_id,"
            " rejection_reason, llm_model)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                did, ts if ts is not None else time.time(), mode, symbol,
                market_regime, action, strategy, confidence, evidence,
                risk_status, tool_name, json.dumps(tool_arguments or {}),
                outcome_status, execution_id,
                json.dumps(market_context or {}, ensure_ascii=False),
                json.dumps(portfolio_context or {}, ensure_ascii=False),
                agent_version, prompt_version, config_version, experiment_id,
                rejection_reason, llm_model,
            ),
        )
        self._conn.commit()
        return did

    def link_execution(self, decision_id: str, execution_id: str) -> None:
        self._conn.execute(
            "UPDATE decision_events SET execution_id = ? WHERE decision_id = ?",
            (execution_id, decision_id),
        )
        self._conn.commit()

    def get_decision_event(self, decision_id: str) -> dict[str, Any] | None:
        self._conn.row_factory = sqlite3.Row
        row = self._conn.execute(
            "SELECT * FROM decision_events WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        self._conn.row_factory = None
        return dict(row) if row else None

    # ---------------------------------------------------------------- trades

    def upsert_trade_event(
        self,
        executor_id: str,
        decision_id: str | None = None,
        ts_open: float | None = None,
        ts_close: float | None = None,
        symbol: str | None = None,
        strategy: str | None = None,
        connector: str | None = None,
        side: str | None = None,
        status: str | None = None,
        close_type: str | None = None,
        pnl_quote: float | None = None,
        pnl_pct: float | None = None,
        filled_amount_quote: float | None = None,
        fees_quote: float | None = None,
        gross_pnl: float | None = None,
        entry_fee: float | None = None,
        exit_fee: float | None = None,
        slippage_quote: float | None = None,
        regime_at_entry: str | None = None,
        source: str | None = None,
        experiment_id: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO trade_events"
            " (executor_id, decision_id, ts_open, ts_close, symbol, strategy,"
            " connector, side, status, close_type, pnl_quote, pnl_pct,"
            " filled_amount_quote, fees_quote, gross_pnl, entry_fee, exit_fee,"
            " slippage_quote, regime_at_entry, source, experiment_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                executor_id, decision_id, ts_open, ts_close, symbol, strategy,
                connector, side, status, close_type, pnl_quote, pnl_pct,
                filled_amount_quote, fees_quote, gross_pnl, entry_fee, exit_fee,
                slippage_quote, regime_at_entry, source, experiment_id,
            ),
        )
        self._conn.commit()

    # --------------------------------------------------------------- reviews

    def record_review_event(
        self,
        decision_id: str | None = None,
        execution_id: str | None = None,
        outcome: str | None = None,
        decision_quality: str | None = None,
        execution_quality: str | None = None,
        regime_accuracy: str | None = None,
        main_error: str | None = None,
        main_success: str | None = None,
        lesson: str | None = None,
        raw: str | None = None,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO review_events"
            " (ts, decision_id, execution_id, outcome, decision_quality,"
            " execution_quality, regime_accuracy, main_error, main_success,"
            " lesson, raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(), decision_id, execution_id, outcome,
                decision_quality, execution_quality, regime_accuracy,
                main_error, main_success, lesson, raw,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    # ----------------------------------------------------------- experiments

    def start_experiment(
        self,
        name: str,
        symbols: list[str] | None = None,
        strategies: list[str] | None = None,
        agent_version: str | None = None,
        prompt_version: str | None = None,
        config_version: str | None = None,
        config_snapshot: dict[str, Any] | None = None,
        notes: str | None = None,
        experiment_id: str | None = None,
        status: str = "running",
    ) -> str:
        eid = experiment_id or f"exp-{uuid.uuid4().hex[:12]}"
        self._conn.execute(
            "INSERT INTO experiments"
            " (experiment_id, name, status, started_at, symbols, strategies,"
            " agent_version, prompt_version, config_version, config_snapshot,"
            " notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                eid, name, status, time.time(),
                json.dumps(symbols or []), json.dumps(strategies or []),
                agent_version, prompt_version, config_version,
                json.dumps(config_snapshot or {}, ensure_ascii=False), notes,
            ),
        )
        self._conn.commit()
        return eid

    def end_experiment(self, experiment_id: str, status: str = "completed") -> None:
        self._conn.execute(
            "UPDATE experiments SET status = ?, ended_at = ? WHERE experiment_id = ?",
            (status, time.time(), experiment_id),
        )
        self._conn.commit()

    def get_active_experiment(self) -> dict[str, Any] | None:
        self._conn.row_factory = sqlite3.Row
        row = self._conn.execute(
            "SELECT * FROM experiments WHERE status = 'running'"
            " ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        self._conn.row_factory = None
        return dict(row) if row else None

    def close(self) -> None:
        self._conn.close()
