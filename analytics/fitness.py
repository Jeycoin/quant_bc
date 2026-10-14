"""Composite experiment fitness (v1.4) — GT-Score-inspired.

Single-metric selection (net PnL or Sharpe alone) overfits: a run can win
on one lucky trade or one quiet window. Following StratEvo's GT-Score idea
(arXiv:2602.00080) the composite folds four independent qualities into one
number:

    fitness = sharpe_component
              × return/drawdown payoff
              × cross-window consistency
              × turnover penalty

- sharpe_component: sign-preserving Sharpe-like ratio of the equity curve
- payoff: total return / max drawdown (Calmar-style), capped so a tiny
  drawdown cannot explode the score
- consistency: fraction of equal time windows with positive net PnL —
  a strategy that made all its money in one window scores low
- turnover penalty: total fees / gross PnL — churn that eats its own
  edge is penalized even when net PnL is positive

Used for experiment comparison and (later) weight-evolution fitness. It is
a ranking device, not a significance test — pre-registered thresholds and
out-of-sample windows still decide promotion.
"""

from __future__ import annotations

import math
from typing import Any


def _window_consistency(trades: list[dict[str, Any]], n_windows: int = 4) -> float | None:
    """Fraction of equal-length time windows with positive net PnL."""
    closed = [t for t in trades
              if t.get("ts_close") and t.get("pnl_quote") is not None]
    if len(closed) < n_windows * 2:
        return None
    t0 = min(t["ts_open"] or t["ts_close"] for t in closed)
    t1 = max(t["ts_close"] for t in closed)
    if t1 <= t0:
        return None
    width = (t1 - t0) / n_windows
    pnl_by_window = [0.0] * n_windows
    count_by_window = [0] * n_windows
    for t in closed:
        idx = min(int((t["ts_close"] - t0) / width), n_windows - 1)
        pnl_by_window[idx] += float(t["pnl_quote"])
        count_by_window[idx] += 1
    active = [p for p, c in zip(pnl_by_window, count_by_window) if c > 0]
    if not active:
        return None
    return sum(1 for p in active if p > 0) / len(active)


def composite_fitness(
    trades: list[dict[str, Any]],
    equity_snapshots: list[dict[str, Any]] | None = None,
    reference_equity: float = 10000.0,
) -> dict[str, Any]:
    """Composite fitness for one experiment arm.

    trades: closed trade dicts (pnl_quote, fees_quote, gross_pnl, ts_*).
    equity_snapshots: optional {ts, equity} curve — when absent, a realized
    curve is rebuilt from closed trades against reference_equity.
    """
    from analytics.metrics import equity_metrics, trading_metrics

    closed = [t for t in trades
              if t.get("ts_close") and t.get("pnl_quote") is not None]
    tm = trading_metrics(trades)

    if equity_snapshots:
        curve = equity_snapshots
    else:
        curve = []
        cum = reference_equity
        for t in sorted(closed, key=lambda x: x["ts_close"]):
            cum += float(t["pnl_quote"])
            curve.append({"ts": t["ts_close"], "equity": cum})
    em = equity_metrics(curve)

    total_ret = em.get("total_return") or 0.0
    max_dd_pct = (em.get("max_drawdown_pct") or 0.0)
    sharpe = em.get("sharpe_like")

    # payoff: Calmar-style magnitude, denominator floored at 0.1% so a
    # near-zero drawdown cannot inflate the score. Sign is carried by the
    # Sharpe term only — a negative return with a negative Sharpe must NOT
    # double-negative back to positive. Sharpe capped at ±5 so a near-zero
    # variance curve cannot dominate the score.
    payoff = abs(total_ret) * 100 / max(max_dd_pct, 0.1)

    consistency = _window_consistency(closed)

    gross = sum(abs(float(t.get("gross_pnl") or t["pnl_quote"])) for t in closed)
    fees = sum(float(t.get("fees_quote") or 0) for t in closed)
    fee_ratio = fees / gross if gross > 0 else 1.0
    turnover_penalty = max(0.0, 1.0 - fee_ratio)

    fitness = None
    if sharpe is not None and consistency is not None and closed:
        # sign-preserving: a negative Sharpe flips the whole score.
        # consistency scales between 0.5 and 1.0 — never zero, so a
        # strategy that loses in EVERY window still scores negative
        consistency_term = 0.5 + 0.5 * consistency
        capped = max(-5.0, min(5.0, sharpe))
        sharpe_term = math.copysign(abs(capped) ** 0.5, capped)
        fitness = sharpe_term * payoff * consistency_term * turnover_penalty

    return {
        "fitness": round(fitness, 4) if fitness is not None else None,
        "components": {
            "sharpe_like": sharpe,
            "total_return_pct": round(total_ret * 100, 4),
            "max_drawdown_pct": round(max_dd_pct, 4),
            "payoff": round(payoff, 4),
            "window_consistency": consistency,
            "fee_ratio": round(fee_ratio, 4),
            "turnover_penalty": round(turnover_penalty, 4),
        },
        "trading": tm,
    }
