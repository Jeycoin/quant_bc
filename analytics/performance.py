"""Daily-granularity performance stats + block bootstrap confidence intervals.

metrics.py computes point statistics from equity snapshots and trade rows.
This module adds what experiment comparison actually needs:

- a daily PnL series builder (replay experiments store no equity curve —
  the realized curve is rebuilt from closed trades at day granularity)
- daily-return risk/efficiency stats: Sharpe, Sortino, Calmar, CVaR-95
  (Expected Shortfall of daily returns), worst day
- moving-block bootstrap CIs that respect time-series correlation
  (trades cluster in regimes; iid resampling of individual trades would
  understate uncertainty)

All functions are pure. The bootstrap uses a fixed seed by default so
reports are reproducible.
"""

from __future__ import annotations

import math
import random
from typing import Any, Callable

DAY_S = 86400


# ------------------------------------------------------------- daily series

def daily_pnl_series(
    trades: list[dict[str, Any]],
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> list[dict[str, Any]]:
    """Aggregate closed trades into per-UTC-day buckets.

    Returns ascending [{day_ts, pnl, fees, trades, wins}] covering every day
    in [start, end] (zero-fill days included — a day with no trades is a
    real observation of the strategy doing nothing, not missing data).
    """
    closed = [t for t in trades
              if t.get("ts_close") and t.get("pnl_quote") is not None]
    if not closed:
        return []
    first = start_ts if start_ts is not None else min(
        t["ts_open"] or t["ts_close"] for t in closed)
    last = end_ts if end_ts is not None else max(t["ts_close"] for t in closed)
    day0 = int(first // DAY_S)
    day1 = int(last // DAY_S)
    buckets = {d: {"day_ts": d * DAY_S, "pnl": 0.0, "fees": 0.0,
                   "trades": 0, "wins": 0}
               for d in range(day0, day1 + 1)}
    for t in closed:
        d = int(t["ts_close"] // DAY_S)
        if d not in buckets:
            continue
        b = buckets[d]
        b["pnl"] += float(t["pnl_quote"])
        b["fees"] += float(t.get("fees_quote") or 0.0)
        b["trades"] += 1
        if float(t["pnl_quote"]) > 0:
            b["wins"] += 1
    return [buckets[d] for d in sorted(buckets)]


# ---------------------------------------------------------- daily statistics

def daily_metrics(
    days: list[dict[str, Any]],
    reference_equity: float = 10000.0,
) -> dict[str, Any]:
    """Point statistics over a daily pnl series (from daily_pnl_series).

    Returns are computed against `reference_equity` (the fixed starting
    capital), not a compounding curve — with one 30d window the difference
    is cosmetic and the fixed base keeps bootstrap resamples comparable.
    """
    if not days:
        return {"n_days": 0}
    pnls = [d["pnl"] for d in days]
    rets = [p / reference_equity for p in pnls]
    n = len(rets)
    total = sum(pnls)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    max_dd_pct = max_dd / reference_equity * 100

    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    vol = math.sqrt(var)
    sharpe = mean / vol if vol > 0 else None
    downside = [r for r in rets if r < 0]
    dvol = math.sqrt(sum(r * r for r in downside) / n) if n else 0.0
    sortino = mean / dvol if dvol > 0 else None
    total_ret = total / reference_equity
    calmar = (total_ret / (max_dd_pct / 100)) if max_dd_pct > 0 else None

    sorted_rets = sorted(rets)
    k = max(1, int(math.ceil(0.05 * n)))
    cvar_95 = sum(sorted_rets[:k]) / k  # mean of the worst 5% days (<= 0)

    return {
        "n_days": n,
        "total_pnl": round(total, 6),
        "avg_daily_pnl": round(total / n, 6),
        "total_return_pct": round(total_ret * 100, 6),
        "max_drawdown_pct": round(max_dd_pct, 6),
        "volatility_daily_pct": round(vol * 100, 6),
        "sharpe_daily": round(sharpe, 6) if sharpe is not None else None,
        "sharpe_annualized": round(sharpe * math.sqrt(365), 6)
        if sharpe is not None else None,
        "sortino_daily": round(sortino, 6) if sortino is not None else None,
        "calmar": round(calmar, 6) if calmar is not None else None,
        "cvar95_daily_pct": round(cvar_95 * 100, 6),
        "worst_day_pct": round(sorted_rets[0] * 100, 6),
        "total_fees": round(sum(d["fees"] for d in days), 6),
    }


# ----------------------------------------------------------------- bootstrap

def moving_block_bootstrap(
    values: list[Any],
    block_len: int,
    n_resamples: int,
    seed: int = 42,
) -> list[list[Any]]:
    """Moving block bootstrap (Künsch): resample overlapping blocks of
    `block_len` consecutive observations to preserve local autocorrelation.

    Block length rule of thumb: ~ n**(1/3); for a 30-day window that is ~3,
    which is also the regime persistence scale we want to preserve.
    """
    n = len(values)
    if n == 0:
        return []
    block_len = max(1, min(block_len, n))
    rng = random.Random(seed)
    out: list[list[Any]] = []
    max_start = n - block_len
    for _ in range(n_resamples):
        sample: list[Any] = []
        while len(sample) < n:
            i = rng.randint(0, max_start)
            sample.extend(values[i:i + block_len])
        out.append(sample[:n])
    return out


def bootstrap_ci(
    values: list[Any],
    stat_fn: Callable[[list[Any]], float | None],
    block_len: int = 3,
    n_resamples: int = 2000,
    seed: int = 42,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Percentile bootstrap CI for `stat_fn` over moving-block resamples."""
    estimate = stat_fn(values)
    if not values or estimate is None:
        return {"estimate": estimate, "ci_lo": None, "ci_hi": None,
                "n_resamples": 0, "block_len": block_len}
    stats = []
    for sample in moving_block_bootstrap(values, block_len, n_resamples, seed):
        s = stat_fn(sample)
        if s is not None and math.isfinite(s):
            stats.append(s)
    if not stats:
        return {"estimate": estimate, "ci_lo": None, "ci_hi": None,
                "n_resamples": 0, "block_len": block_len}
    stats.sort()
    lo = stats[int(len(stats) * alpha / 2)]
    hi = stats[min(len(stats) - 1, int(len(stats) * (1 - alpha / 2)))]
    return {"estimate": round(estimate, 6),
            "ci_lo": round(lo, 6), "ci_hi": round(hi, 6),
            "n_resamples": len(stats), "block_len": block_len}
