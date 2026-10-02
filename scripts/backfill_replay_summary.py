"""Backfill summary_json for completed replay experiments.

Recomputes the replay window, agent equity metrics and the three baselines
(fixed grid / fixed trend EMA cross / buy&hold) offline from stored trade
events + fresh Hyperliquid candles. No LLM calls, no Hummingbot access.
Old experiments' trades/decisions are never modified — this only fills the
additive experiments.summary_json column.

Usage:
  python scripts/backfill_replay_summary.py <experiment_id> [--days N]
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics import metrics as m  # noqa: E402
from analytics.store import AnalyticsStore  # noqa: E402
from scripts.backtest_agent import (  # noqa: E402
    GRID_SIZE, GRID_TP, POSITION_SIZE, POS_TIME_LIMIT_S, SimPosition,
)
from scripts.backtest_grid import FEE_RATE, GridSim, fetch_candles  # noqa: E402


def _ema(vals: list[float], n: int) -> float:
    k = 2 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


async def backfill(experiment_id: str) -> None:
    db = sqlite3.connect("data/analytics.db")
    db.row_factory = sqlite3.Row
    exp = db.execute(
        "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
    ).fetchone()
    if not exp:
        raise SystemExit(f"experiment {experiment_id} not found")
    trades = db.execute(
        "SELECT * FROM trade_events WHERE experiment_id = ? AND source = 'agent'"
        " ORDER BY ts_open", (experiment_id,),
    ).fetchall()
    if not trades:
        raise SystemExit("no agent trades — nothing to backfill")

    symbols = sorted({t["symbol"] for t in trades})
    start_ts = min(t["ts_open"] for t in trades) - 86400  # generous margin
    end_ts = max(t["ts_close"] or t["ts_open"] for t in trades) + 3600
    days = (end_ts - start_ts) / 86400 + 1

    candles: dict[str, list[dict]] = {}
    for sym in symbols:
        all_c = await fetch_candles(sym, int(days) + 3)
        candles[sym] = [c for c in all_c if start_ts <= c["timestamp"] <= end_ts]
        if not candles[sym]:
            raise SystemExit(f"no candles for {sym} in replayed window")
        # trim to the exact traded window per symbol
        sym_trades = [t for t in trades if t["symbol"] == sym]
        s0 = min(t["ts_open"] for t in sym_trades)
        s1 = max(t["ts_close"] or t["ts_open"] for t in sym_trades)
        candles[sym] = [c for c in candles[sym] if s0 - 3600 <= c["timestamp"] <= s1 + 3600]

    # reconstruct agent equity curve from closed trades
    start_equity = 10000.0
    closed = sorted(
        (t for t in trades if t["ts_close"] is not None), key=lambda t: t["ts_close"]
    )
    equity = start_equity
    curve = [{"ts": start_ts, "equity": equity}]
    for t in closed:
        equity += t["pnl_quote"] or 0
        curve.append({"ts": t["ts_close"], "equity": equity})
    agent_eq = m.equity_metrics(curve)

    baselines: dict[str, dict] = {}
    for sym in symbols:
        window = candles[sym]
        first, last = float(window[0]["close"]), float(window[-1]["close"])

        base = GridSim(first * 0.97, first * 1.03, GRID_SIZE, tp=GRID_TP)
        for c in window:
            base.process_candle(c)
        fixed_grid_pnl = base.equity(last) - GRID_SIZE

        trend_pnl, trend_trades, trend_pos = 0.0, 0, None
        closes_so_far: list[float] = []
        for c in window:
            closes_so_far.append(float(c["close"]))
            if trend_pos is not None:
                result = trend_pos.check_exit(c, c["timestamp"])
                if result:
                    pnl, _reason, _fees = result
                    trend_pnl += pnl
                    trend_trades += 1
                    trend_pos = None
            elif len(closes_so_far) >= 80 and \
                    _ema(closes_so_far[-40:], 20) > _ema(closes_so_far[-80:], 50):
                px_now = float(c["close"])
                trend_pos = SimPosition(
                    sym, "LONG", px_now, POSITION_SIZE / px_now,
                    c["timestamp"] + POS_TIME_LIMIT_S, c["timestamp"],
                    f"bt-backfill-trend-{sym}-{int(c['timestamp'])}", None)
        if trend_pos is not None:
            fees = (trend_pos.entry + last) * trend_pos.qty * FEE_RATE
            trend_pnl += (last - trend_pos.entry) * trend_pos.qty - fees
            trend_trades += 1

        baselines[sym] = {
            "fixed_grid_pnl": round(fixed_grid_pnl, 4),
            "fixed_trend_pnl": round(trend_pnl, 4),
            "fixed_trend_trades": trend_trades,
            "buy_hold_pnl": round(GRID_SIZE * (last / first - 1), 4),
        }

    store = AnalyticsStore("data/analytics.db")
    store.set_experiment_summary(experiment_id, {
        "replayed_window": {"start_ts": start_ts + 86400 - 3600,
                            "end_ts": end_ts - 3600},
        "start_equity": start_equity,
        "final_equity": curve[-1]["equity"],
        "agent_equity_metrics": agent_eq,
        "trade_count": len(trades),
        "baselines": baselines,
        "baseline_notional": {"fixed_grid": GRID_SIZE, "trend": POSITION_SIZE,
                              "buy_hold": GRID_SIZE},
        "backfilled": True,
    })
    store.close()
    print(f"{experiment_id}: summary backfilled "
          f"(return {agent_eq['total_return'] and round(agent_eq['total_return'] * 100, 2)}%, "
          f"maxDD {agent_eq['max_drawdown_pct'] and round(agent_eq['max_drawdown_pct'], 2)}%)")
    for sym, b in baselines.items():
        print(f"  {sym}: grid {b['fixed_grid_pnl']:+.2f} trend {b['fixed_trend_pnl']:+.2f} "
              f"b&h {b['buy_hold_pnl']:+.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("experiment_id")
    args = ap.parse_args()
    asyncio.run(backfill(args.experiment_id))
