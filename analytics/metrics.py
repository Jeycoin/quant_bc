"""Performance analytics: pure-metric computation over recorded events.

No trading logic here — only statistics over trade_events, equity snapshots
and order records. All functions are pure (data in, metrics out) so they
can be unit-tested without Hummingbot or the dashboard.
"""

from __future__ import annotations

import math
from typing import Any

DAY_S = 86400


def trading_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Win rate / profit factor / expectancy / holding time over closed trades."""
    closed = [
        t for t in trades
        if t.get("ts_close") and t.get("pnl_quote") is not None
    ]
    pnls = [float(t["pnl_quote"]) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    durations = [
        t["ts_close"] - t["ts_open"]
        for t in closed
        if t.get("ts_open") and t.get("ts_close")
    ]
    return {
        "trade_count": len(trades),
        "closed_count": len(closed),
        "total_pnl_quote": round(sum(pnls), 8) if pnls else 0.0,
        "win_rate": (len(wins) / len(pnls)) if pnls else None,
        "avg_win": (gross_win / len(wins)) if wins else None,
        "avg_loss": (sum(losses) / len(losses)) if losses else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "expectancy": (sum(pnls) / len(pnls)) if pnls else None,
        "avg_holding_s": (sum(durations) / len(durations)) if durations else None,
        "total_fees_quote": round(
            sum(float(t.get("fees_quote") or 0) for t in trades), 8
        ),
    }


def _returns(series: list[float]) -> list[float]:
    return [
        (series[i] - series[i - 1]) / series[i - 1]
        for i in range(1, len(series))
        if series[i - 1]
    ]


def equity_metrics(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Return / drawdown / volatility from equity snapshots (ts, equity)."""
    points = sorted(
        ((float(s["ts"]), float(s["equity"])) for s in snapshots),
        key=lambda p: p[0],
    )
    if len(points) < 2:
        return {
            "total_return": None, "daily_return": None, "weekly_return": None,
            "max_drawdown_pct": None, "volatility_daily": None,
            "sharpe_like": None, "snapshot_count": len(points),
        }

    equities = [p[1] for p in points]
    first, last = equities[0], equities[-1]
    total_return = (last - first) / first if first else None

    span_s = points[-1][0] - points[0][0]
    daily_return = total_return * DAY_S / span_s if total_return is not None and span_s > 0 else None
    weekly_return = daily_return * 7 if daily_return is not None else None

    peak = equities[0]
    max_dd = 0.0
    for eq in equities:
        peak = max(peak, eq)
        if peak:
            max_dd = max(max_dd, (peak - eq) / peak)

    rets = _returns(equities)
    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        vol = math.sqrt(var)
        # snapshots are ~1/minute; scale to a per-day Sharpe-like ratio
        per_day = max(1.0, span_s / DAY_S)
        sharpe_like = (total_return / per_day) / (vol * math.sqrt(len(rets) / per_day)) if vol else None
    else:
        vol = None
        sharpe_like = None

    return {
        "total_return": total_return,
        "daily_return": daily_return,
        "weekly_return": weekly_return,
        "max_drawdown_pct": max_dd * 100,
        "volatility_daily": vol,
        "sharpe_like": sharpe_like,
        "snapshot_count": len(points),
    }


def execution_metrics(orders: list[dict[str, Any]]) -> dict[str, Any]:
    """Fill / cancel / failure rates and indicative slippage from order rows."""
    if not orders:
        return {
            "order_count": 0, "fill_rate": None, "cancel_rate": None,
            "failure_rate": None, "avg_slippage_pct": None,
        }
    filled, cancelled, failed = 0, 0, 0
    slippages: list[float] = []
    for o in orders:
        status = str(o.get("status") or "").upper()
        if "FILLED" in status:
            filled += 1
        elif "CANCEL" in status:
            cancelled += 1
        elif "FAIL" in status or "ERROR" in status:
            failed += 1
        price = o.get("price")
        fill = o.get("average_fill_price")
        if price and fill:
            slippages.append(abs(fill - price) / price * 100)
    n = len(orders)
    return {
        "order_count": n,
        "fill_rate": filled / n,
        "cancel_rate": cancelled / n,
        "failure_rate": failed / n,
        "avg_slippage_pct": (sum(slippages) / len(slippages)) if slippages else None,
    }


def exposure_metrics(positions: list[dict[str, Any]], equity: float | None) -> dict[str, Any]:
    """Current exposure and concentration from open positions."""
    gross = 0.0
    by_symbol: dict[str, float] = {}
    for p in positions:
        notional = abs(float(p.get("amount") or 0)) * float(p.get("mark_price") or p.get("entry_price") or 0)
        gross += notional
        by_symbol[p.get("trading_pair", "?")] = (
            by_symbol.get(p.get("trading_pair", "?"), 0.0) + notional
        )
    concentration = (max(by_symbol.values()) / gross) if gross else None
    return {
        "gross_exposure": gross,
        "exposure_pct": (gross / equity * 100) if equity else None,
        "position_concentration": concentration,
        "by_symbol": by_symbol,
    }
