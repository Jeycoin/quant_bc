"""Experiment report generator (master prompt §35).

Pure offline rendering from analytics.db (+ dashboard.db equity snapshots
when available). No Hummingbot access — a report must be generatable even
when the trading stack is down. All metrics come from analytics/metrics.py
so the report and the dashboard can never disagree.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from analytics import metrics as m
from analytics import queries

REPO_ROOT = Path(__file__).resolve().parent.parent
DASH_DB = REPO_ROOT / "data" / "dashboard.db"


def _pct(x: float | None) -> str:
    return f"{x * 100:.2f}%" if x is not None else "—"


def _usd(x: float | None) -> str:
    return f"${x:,.4f}" if x is not None else "—"


def _hours(s: float | None) -> str:
    return f"{s / 3600:.1f}h" if s is not None else "—"


def _equity_snapshots(since: float | None, until: float | None) -> list[dict[str, Any]]:
    if not DASH_DB.exists():
        return []
    conn = sqlite3.connect(DASH_DB)
    try:
        sql = "SELECT ts, equity FROM equity_snapshots WHERE 1=1"
        params: list[Any] = []
        if since:
            sql += " AND ts >= ?"
            params.append(since)
        if until:
            sql += " AND ts <= ?"
            params.append(until)
        sql += " ORDER BY ts"
        return [{"ts": r[0], "equity": r[1]} for r in conn.execute(sql, params)]
    finally:
        conn.close()


def generate_report(db_path: str, experiment_id: str) -> str:
    exp = queries._row(db_path, "SELECT * FROM experiments WHERE experiment_id = ?",
                       (experiment_id,))
    if not exp:
        return f"# Experiment {experiment_id}\n\nnot found\n"

    started = exp["started_at"]
    ended = exp.get("ended_at") or time.time()
    days = (ended - started) / 86400

    decisions = queries.list_decision_events(db_path, experiment_id=experiment_id, limit=10000)
    trades = queries.list_trade_events(db_path, experiment_id=experiment_id, limit=10000)
    reviews = [
        r for r in queries.list_review_events(db_path, limit=10000)
        if any(t["executor_id"] == r.get("execution_id") for t in trades)
    ]
    dist = queries.decision_distribution(db_path, experiment_id)
    by_strategy = queries.attribution(db_path, "strategy", experiment_id)
    by_regime = queries.attribution(db_path, "regime_at_entry", experiment_id)
    equity = m.equity_metrics(_equity_snapshots(started, ended))
    trading = m.trading_metrics(trades)

    agent_trades = [t for t in trades if t.get("source") == "agent"]
    baseline_trades = [t for t in trades if t.get("source") == "baseline"]
    comparison = {
        "ai_assisted": m.trading_metrics(agent_trades),
        "baseline": m.trading_metrics(baseline_trades),
    }

    total_decisions = sum(dist.values()) or 1
    wait_ratio = dist.get("WAIT", 0) / total_decisions
    reject_ratio = dist.get("REJECT", 0) / total_decisions
    quality_counts: dict[str, int] = {}
    for r in reviews:
        q = r.get("decision_quality") or "unreviewed"
        quality_counts[q] = quality_counts.get(q, 0) + 1

    regimes: dict[str, int] = {}
    for d in decisions:
        if d.get("market_regime"):
            regimes[d["market_regime"]] = regimes.get(d["market_regime"], 0) + 1

    lines = [
        f"# Experiment Report: {exp['name']}",
        "",
        f"- experiment_id: `{experiment_id}`",
        f"- status: {exp['status']}",
        f"- period: {time.strftime('%Y-%m-%d %H:%M', time.localtime(started))}"
        f" → {time.strftime('%Y-%m-%d %H:%M', time.localtime(ended))} ({days:.1f} days)",
        f"- symbols: {exp.get('symbols')}",
        f"- strategies: {exp.get('strategies')}",
        f"- versions: agent={exp.get('agent_version')} prompt={exp.get('prompt_version')}"
        f" config={exp.get('config_version')}",
        "",
        "## Market",
        "",
        f"- regimes observed (from decision events): "
        + (", ".join(f"{k}×{v}" for k, v in sorted(regimes.items())) or "—"),
        "",
        "## Agent",
        "",
        f"- decisions: {total_decisions} ({json.dumps(dist)})",
        f"- WAIT ratio: {_pct(wait_ratio)} · REJECT ratio: {_pct(reject_ratio)}",
        f"- trades executed: {trading['closed_count']} of {trading['trade_count']}",
        "",
        "## Strategy",
        "",
        "| strategy | trades | closed | pnl | fees | win rate |",
        "|---|---|---|---|---|---|",
        *[
            f"| {r['bucket']} | {r['trade_count']} | {r['closed_count']} "
            f"| {_usd(r['total_pnl_quote'])} | {_usd(r['total_fees_quote'])} "
            f"| {_pct(r['win_rate'])} |"
            for r in by_strategy
        ],
        "",
        "## Market Regime Attribution",
        "",
        "| regime | trades | closed | pnl | win rate |",
        "|---|---|---|---|---|",
        *[
            f"| {r['bucket']} | {r['trade_count']} | {r['closed_count']} "
            f"| {_usd(r['total_pnl_quote'])} | {_pct(r['win_rate'])} |"
            for r in by_regime
        ],
        "",
        "## Execution",
        "",
        f"- filled volume: {_usd(sum(t.get('filled_amount_quote') or 0 for t in trades))}",
        f"- total fees: {_usd(trading['total_fees_quote'])}",
        "",
        "## Risk",
        "",
        f"- max drawdown: {_pct((equity['max_drawdown_pct'] or 0) / 100 if equity['max_drawdown_pct'] is not None else None)}",
        f"- equity snapshots: {equity['snapshot_count']}",
        "",
        "## AI Decision Quality",
        "",
        f"- reviews: {len(reviews)} ({json.dumps(quality_counts)})",
        "",
        "## Comparison: AI Assisted vs Baseline",
        "",
        "| metric | AI assisted | baseline |",
        "|---|---|---|",
        f"| trades | {comparison['ai_assisted']['closed_count']} | {comparison['baseline']['closed_count']} |",
        f"| total pnl | {_usd(comparison['ai_assisted']['total_pnl_quote'])} | {_usd(comparison['baseline']['total_pnl_quote'])} |",
        f"| win rate | {_pct(comparison['ai_assisted']['win_rate'])} | {_pct(comparison['baseline']['win_rate'])} |",
        f"| profit factor | {comparison['ai_assisted']['profit_factor'] or '—'} | {comparison['baseline']['profit_factor'] or '—'} |",
        f"| expectancy | {_usd(comparison['ai_assisted']['expectancy'])} | {_usd(comparison['baseline']['expectancy'])} |",
        f"| avg holding | {_hours(comparison['ai_assisted']['avg_holding_s'])} | {_hours(comparison['baseline']['avg_holding_s'])} |",
        "",
        "> Caution (§36): a higher AI PnL does not prove AI is better — consider"
        " sample size, period, market regime, fees, slippage and parameter"
        " differences before drawing conclusions.",
        "",
    ]
    return "\n".join(lines)
