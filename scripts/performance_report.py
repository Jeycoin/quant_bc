"""Performance report with bootstrap confidence intervals.

Usage:
  python scripts/performance_report.py --exp exp-f2aafe9b4496 \
      [--baseline exp-5bdbd6aa3ceb] [--resamples 2000] [--block-days 3] \
      [--seed 42] [--json-out data/analysis/performance.json]

Prints ASCII SEC| lines and optionally dumps the full result as JSON.
Point metrics reuse analytics.metrics.trading_metrics; daily stats and CIs
come from analytics.performance.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analytics import metrics as m          # noqa: E402
from analytics import performance as perf   # noqa: E402

DB = REPO / "data" / "analytics.db"
REFERENCE_EQUITY = 10000.0


def load_trades(con, exp_id):
    return [dict(r) for r in con.execute(
        "SELECT * FROM trade_events WHERE experiment_id=? AND source='agent' "
        "AND ts_close IS NOT NULL AND pnl_quote IS NOT NULL", (exp_id,))]


def experiment_window(con, exp_id):
    r = con.execute(
        "SELECT MIN(ts) mn, MAX(ts) mx FROM decision_events "
        "WHERE experiment_id=? AND symbol IS NOT NULL", (exp_id,)).fetchone()
    return (r[0], r[1]) if r and r[0] else (None, None)


def bucket_stats(rows):
    n = len(rows)
    if not n:
        return None
    net = sum(r["pnl_quote"] for r in rows)
    wins = sum(1 for r in rows if r["pnl_quote"] > 0)
    fees = sum(r["fees_quote"] or 0 for r in rows)
    gross = sum((r["gross_pnl"] if r["gross_pnl"] is not None
                 else r["pnl_quote"] + (r["fees_quote"] or 0)) for r in rows)
    return {"n": n, "net": round(net, 4), "win_rate": round(wins / n, 4),
            "fees": round(fees, 4),
            "fee_gross_ratio": round(fees / gross, 4) if gross > 0 else None,
            "insufficient": n < 10}


def analyze(con, exp_id, resamples, block_days, seed):
    trades = load_trades(con, exp_id)
    start_ts, end_ts = experiment_window(con, exp_id)
    tm = m.trading_metrics(trades)
    days = perf.daily_pnl_series(trades, start_ts=start_ts, end_ts=end_ts)
    dm = perf.daily_metrics(days, reference_equity=REFERENCE_EQUITY)

    pnls = [d["pnl"] for d in days]

    def stat_net(s):
        return sum(s)

    def stat_sharpe(s):
        r = perf.daily_metrics(
            [{"pnl": v, "fees": 0.0, "trades": 0, "wins": 0, "day_ts": 0}
             for v in s], REFERENCE_EQUITY)
        return r.get("sharpe_daily")

    def stat_maxdd(s):
        r = perf.daily_metrics(
            [{"pnl": v, "fees": 0.0, "trades": 0, "wins": 0, "day_ts": 0}
             for v in s], REFERENCE_EQUITY)
        return r.get("max_drawdown_pct")

    cis = {
        "net_pnl": perf.bootstrap_ci(pnls, stat_net, block_days, resamples, seed),
        "sharpe_daily": perf.bootstrap_ci(pnls, stat_sharpe, block_days,
                                          resamples, seed),
        "max_drawdown_pct": perf.bootstrap_ci(pnls, stat_maxdd, block_days,
                                              resamples, seed),
    }
    # win rate / expectancy: day-block bootstrap keeps trade clustering
    day_obs = [(d["wins"], d["trades"], d["pnl"]) for d in days]

    def stat_wr(s):
        w = sum(x[0] for x in s)
        t = sum(x[1] for x in s)
        return w / t if t else None

    def stat_exp(s):
        t = sum(x[1] for x in s)
        return sum(x[2] for x in s) / t if t else None

    cis["win_rate"] = perf.bootstrap_ci(day_obs, stat_wr, block_days,
                                        resamples, seed)
    cis["expectancy"] = perf.bootstrap_ci(day_obs, stat_exp, block_days,
                                          resamples, seed)

    by_symbol = {k: bucket_stats(v) for k, v in sorted(
        _groupby(trades, lambda t: t["symbol"]).items())}
    by_regime = {k: bucket_stats(v) for k, v in sorted(
        _groupby(trades, lambda t: t["regime_at_entry"] or "UNKNOWN").items())}
    by_month = {k: bucket_stats(v) for k, v in sorted(
        _groupby(trades, lambda t: time.strftime("%Y-%m", time.gmtime(t["ts_close"]))).items())}

    gross = sum((t["gross_pnl"] if t["gross_pnl"] is not None
                 else t["pnl_quote"] + (t["fees_quote"] or 0)) for t in trades)
    return {
        "experiment_id": exp_id,
        "window": {"start_ts": start_ts, "end_ts": end_ts,
                   "days": round((end_ts - start_ts) / 86400, 2)
                   if start_ts and end_ts else None},
        "trading": tm,
        "daily": dm,
        "gross_pnl": round(gross, 4),
        "fee_gross_ratio": round(tm["total_fees_quote"] / gross, 4)
        if gross > 0 else None,
        "ci": cis,
        "by_symbol": by_symbol,
        "by_regime": by_regime,
        "by_month": by_month,
    }


def _groupby(rows, key):
    out = defaultdict(list)
    for r in rows:
        out[key(r)].append(r)
    return out


def print_exp(res):
    e = res["experiment_id"]
    tm, dm, ci = res["trading"], res["daily"], res["ci"]

    def fci(name, scale=1.0):
        c = ci[name]
        if c["estimate"] is None:
            return "n/a"
        return (f"{c['estimate'] * scale:.4f} "
                f"[{c['ci_lo'] * scale:.4f}, {c['ci_hi'] * scale:.4f}]")

    print(f"SEC|EXP|{e}|window_days={res['window']['days']}|"
          f"trades={tm['closed_count']}")
    print(f"SEC|RETURN|{e}|net={tm['total_pnl_quote']:.4f}|"
          f"avg_daily={dm.get('avg_daily_pnl')}|"
          f"total_ret_pct={dm.get('total_return_pct')}|ci_net={fci('net_pnl')}")
    print(f"SEC|RISK|{e}|maxdd_pct={dm.get('max_drawdown_pct')}|"
          f"ci_maxdd={fci('max_drawdown_pct')}|vol_daily_pct={dm.get('volatility_daily_pct')}|"
          f"cvar95_daily_pct={dm.get('cvar95_daily_pct')}|"
          f"worst_day_pct={dm.get('worst_day_pct')}")
    print(f"SEC|EFFICIENCY|{e}|sharpe_d={dm.get('sharpe_daily')}|"
          f"ci_sharpe={fci('sharpe_daily')}|sharpe_ann={dm.get('sharpe_annualized')}|"
          f"sortino_d={dm.get('sortino_daily')}|calmar={dm.get('calmar')}")
    print(f"SEC|QUALITY|{e}|wr={tm['win_rate']}|ci_wr={fci('win_rate')}|"
          f"avg_win={tm['avg_win']}|avg_loss={tm['avg_loss']}|"
          f"payoff={abs(tm['avg_win'] / tm['avg_loss']) if tm['avg_loss'] else None}|"
          f"expectancy={tm['expectancy']}|ci_exp={fci('expectancy')}|"
          f"pf={tm['profit_factor']}")
    print(f"SEC|COST|{e}|fees={tm['total_fees_quote']:.4f}|"
          f"gross={res['gross_pnl']}|fgr={res['fee_gross_ratio']}")
    for dim in ("by_symbol", "by_regime", "by_month"):
        for k, v in res[dim].items():
            if v is None:
                continue
            print(f"SEC|{dim.upper()}|{e}|{k}|n={v['n']}|net={v['net']}|"
                  f"wr={v['win_rate']}|fgr={v['fee_gross_ratio']}"
                  + ("|INSUFFICIENT" if v["insufficient"] else ""))


def main():
    ap = argparse.ArgumentParser(description="Performance report with bootstrap CIs")
    ap.add_argument("--exp", required=True)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--resamples", type=int, default=2000)
    ap.add_argument("--block-days", type=int, default=3)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    out = {"meta": {"resamples": args.resamples, "block_days": args.block_days,
                    "seed": args.seed, "reference_equity": REFERENCE_EQUITY}}
    for tag, eid in (("exp", args.exp), ("baseline", args.baseline)):
        if not eid:
            continue
        res = analyze(con, eid, args.resamples, args.block_days, args.seed)
        out[tag] = res
        print_exp(res)
    con.close()
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        print(f"SEC|WROTE|{p}")


if __name__ == "__main__":
    main()
