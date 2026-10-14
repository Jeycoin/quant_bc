"""Post-hoc circuit breakers (v1.4) — Freqtrade Protections adapted.

The five existing gates (safety / entry / grid / cost / risk) are all
*pre-trade*: they judge a proposal against the current market. None of them
reacts to what the system itself just did. The 30d replay showed the cost of
that hole: 51 time-stopped trades churned -52.98 USDT because nothing said
"we just lost N times in a row — stop trading for a while".

Four breakers, all evaluated from closed-trade outcomes (not LLM state):

- Cooldown        — per-symbol lock after a close; longer after a stop-out
                    (replaces the ad-hoc cooldown_until in backtest_agent)
- StoplossGuard   — >= trade_limit losing closes inside lookback -> global
                    pause (deteriorating regime, not bad luck)
- MaxDrawdown     — realized-PnL drawdown inside lookback exceeds limit ->
                    global pause
- LowProfitPairs  — a symbol whose rolling closed PnL stays below the
                    required floor gets blacklisted for stop_duration

Pure functions over plain dicts so the backtest (in-memory trade list) and
the live agent (analytics store rows) share identical logic. Fail-open only
when the config disables a breaker; empty trade history = no locks.

Nothing here calls an LLM. All thresholds live in config/settings.yaml
under `protections:`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STOP_CLOSE_TYPES = {"STOP_LOSS", "LIQUIDATION", "TIME_STOP", "TRAILING_STOP",
                    "GRID_PROTECTION_EXIT"}


@dataclass
class ProtectionReport:
    global_lock: bool = False
    reasons: list[str] = field(default_factory=list)
    symbol_locks: dict[str, str] = field(default_factory=dict)
    # key "SYM" applies to grids+positions; "SYM:LONG" side-specific
    until_ts: float | None = None

    def blocks(self, symbol: str | None, side: str | None = None) -> str | None:
        """Rejection reason if (symbol, side) is locked, else None."""
        if self.global_lock:
            return "; ".join(self.reasons) or "global protection lock"
        if symbol:
            if symbol in self.symbol_locks:
                return self.symbol_locks[symbol]
            if side and f"{symbol}:{side}" in self.symbol_locks:
                return self.symbol_locks[f"{symbol}:{side}"]
        return None


def _cfg(config: dict[str, Any] | None) -> dict[str, Any]:
    return (config or {}).get("protections", {})


def evaluate_protections(
    closed_trades: list[dict[str, Any]],
    now_ts: float,
    config: dict[str, Any] | None = None,
    reference_equity: float = 10000.0,
) -> ProtectionReport:
    """closed_trades: dicts with symbol, side, pnl_quote, close_type,
    ts_close (ascending or unsorted — sorted internally). now_ts: the
    decision timestamp (bar time in replay, wall clock live)."""
    cfg = _cfg(config)
    if not cfg.get("enabled", True):
        return ProtectionReport()

    trades = sorted(
        (t for t in closed_trades
         if t.get("ts_close") is not None and t["ts_close"] <= now_ts),
        key=lambda t: t["ts_close"],
    )
    report = ProtectionReport()
    if not trades:
        return report

    # ---------------------------------------------------------- cooldown
    cooldown_h = float(cfg.get("cooldown_hours", 0) or 0)
    cooldown_stop_h = float(cfg.get("cooldown_stop_hours", 6) or 0)
    for t in trades:
        sym, side = t.get("symbol"), (t.get("side") or "").upper()
        pnl = t.get("pnl_quote")
        stopped = (t.get("close_type") in STOP_CLOSE_TYPES
                   or (pnl is not None and pnl < 0))
        lock_h = cooldown_stop_h if stopped else cooldown_h
        if lock_h <= 0:
            continue
        until = t["ts_close"] + lock_h * 3600
        if until <= now_ts:
            continue
        remain = (until - now_ts) / 3600
        reason = (f"cooldown: {sym} closed {t.get('close_type') or 'trade'} "
                  f"{remain:.1f}h ago-lock ({lock_h:g}h)")
        key = f"{sym}:{side}" if stopped and side else sym
        report.symbol_locks[key] = reason
        report.until_ts = max(report.until_ts or 0, until)

    # ------------------------------------------------------ stoploss guard
    sg = cfg.get("stoploss_guard", {}) or {}
    sg_lookback = float(sg.get("lookback_hours", 24)) * 3600
    sg_limit = int(sg.get("trade_limit", 4))
    sg_stop = float(sg.get("stop_duration_hours", 6)) * 3600
    if sg.get("enabled", True) and sg_limit > 0:
        recent = [t for t in trades if now_ts - t["ts_close"] <= sg_lookback]
        losses = [t for t in recent
                  if (t.get("pnl_quote") or 0) < 0
                  or t.get("close_type") in STOP_CLOSE_TYPES]
        if len(losses) >= sg_limit:
            until = losses[-1]["ts_close"] + sg_stop
            if until > now_ts:
                report.global_lock = True
                report.reasons.append(
                    f"stoploss guard: {len(losses)} losing closes in "
                    f"{sg_lookback / 3600:g}h (>= {sg_limit}) — paused "
                    f"{(until - now_ts) / 3600:.1f}h")
                report.until_ts = max(report.until_ts or 0, until)

    # -------------------------------------------------------- max drawdown
    md = cfg.get("max_drawdown", {}) or {}
    md_lookback = float(md.get("lookback_hours", 72)) * 3600
    md_max_pct = float(md.get("max_allowed_pct", 3.0))
    md_stop = float(md.get("stop_duration_hours", 12)) * 3600
    if md.get("enabled", True) and reference_equity > 0:
        window = [t for t in trades if now_ts - t["ts_close"] <= md_lookback]
        if len(window) >= 3:
            cum = 0.0
            peak = 0.0
            max_dd = 0.0
            trough_ts = None
            for t in window:
                cum += t.get("pnl_quote") or 0.0
                peak = max(peak, cum)
                if peak - cum > max_dd:
                    max_dd = peak - cum
                    trough_ts = t["ts_close"]
            dd_pct = max_dd / reference_equity * 100
            if dd_pct >= md_max_pct and trough_ts is not None:
                until = trough_ts + md_stop
                if until > now_ts:
                    report.global_lock = True
                    report.reasons.append(
                        f"max drawdown: realized DD {dd_pct:.2f}% >= "
                        f"{md_max_pct}% over {md_lookback / 3600:g}h — paused "
                        f"{(until - now_ts) / 3600:.1f}h")
                    report.until_ts = max(report.until_ts or 0, until)

    # ----------------------------------------------------- low-profit pairs
    lp = cfg.get("low_profit_pairs", {}) or {}
    lp_lookback = float(lp.get("lookback_hours", 168)) * 3600
    lp_min = int(lp.get("min_trades", 4))
    lp_req = float(lp.get("required_profit_quote", 0.0))
    lp_stop = float(lp.get("stop_duration_hours", 72)) * 3600
    if lp.get("enabled", True) and lp_min > 0:
        by_sym: dict[str, list[dict]] = {}
        for t in trades:
            if t.get("symbol") and now_ts - t["ts_close"] <= lp_lookback:
                by_sym.setdefault(t["symbol"], []).append(t)
        for sym, rows in by_sym.items():
            if len(rows) < lp_min:
                continue
            total = sum(r.get("pnl_quote") or 0.0 for r in rows)
            if total >= lp_req:
                continue
            until = rows[-1]["ts_close"] + lp_stop
            if until > now_ts and sym not in report.symbol_locks:
                report.symbol_locks[sym] = (
                    f"low profit: {sym} {len(rows)} trades netted "
                    f"{total:+.2f} < {lp_req} over {lp_lookback / 3600:g}h "
                    f"— blacklisted {(until - now_ts) / 3600:.1f}h")
                report.until_ts = max(report.until_ts or 0, until)

    return report
