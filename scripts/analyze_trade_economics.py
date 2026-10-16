"""Trade economics validation: per-trade fee accounting + bucketed net PnL.

Reads data/analytics.db (read-only) and the live executor snapshot
(data/live_executors_snapshot.json, fetched from the Dashboard API).

Outputs ASCII-only SEC| lines to stdout and a JSON blob to
data/analysis/trade_economics.json for report writing.

Fee model notes (discovered during validation):
- The replay harness (scripts/backtest_grid.py) charges FEE_RATE = 0.0004
  per side per fill, flat, no slippage. config/settings.yaml declares
  maker 0.0002 / taker 0.0005 per side. These are NOT the same model:
  grids are simulated 2x more expensive than the maker rate the Cost
  Validator approves against; positions are simulated 0.8x the taker rate.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "analytics.db"
OUT = REPO / "data" / "analysis" / "trade_economics.json"
LIVE_JSON = REPO / "data" / "live_executors_snapshot.json"

REPLAY_FEE = 0.0004        # backtest_grid.FEE_RATE, per side per fill
MAKER = 0.0002             # config cost.maker_fee
TAKER = 0.0005             # config cost.taker_fee
SLIP = 0.0002              # config cost.slippage_pct (per round trip in cfg;
                           # used here per side as instructed by the task)
FUNDING_INTERVAL_H = 8.0

PRIMARY = "exp-f2aafe9b4496"     # 30d LLM replay (glm-4.5-air)
SECONDARY = "exp-5bdbd6aa3ceb"   # 30d rule-proposer replay
FIRST_7D = "exp-a856ad913012"    # original 7d replay (the pain point)


def load_trades(con, experiment_id):
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM trade_events WHERE experiment_id=? AND source='agent'",
        (experiment_id,))]
    return rows


def load_decisions_by_exec(con, experiment_id):
    """executor_id (decision_events.execution_id) -> decision row (deduped)."""
    out = {}
    for r in con.execute(
            "SELECT * FROM decision_events WHERE experiment_id=? "
            "AND execution_id IS NOT NULL", (experiment_id,)):
        d = dict(r)
        if d["execution_id"] not in out:
            out[d["execution_id"]] = d
    return out


def mc_of(dec):
    if not dec:
        return {}
    try:
        return json.loads(dec.get("market_context") or "{}")
    except Exception:
        return {}


def portfolio_of(dec):
    if not dec:
        return {}
    try:
        return json.loads(dec.get("portfolio_context") or "{}")
    except Exception:
        return {}


def bucket_stats(rows):
    n = len(rows)
    if not n:
        return None
    net = sum(r["pnl_quote"] for r in rows)
    fees = sum(r["fees_quote"] or 0 for r in rows)
    gross = sum((r["gross_pnl"] if r["gross_pnl"] is not None
                 else r["pnl_quote"] + (r["fees_quote"] or 0)) for r in rows)
    wins = sum(1 for r in rows if r["pnl_quote"] > 0)
    return {
        "n": n,
        "net": round(net, 4),
        "avg": round(net / n, 4),
        "win_rate": round(wins / n, 4),
        "fees": round(fees, 4),
        "gross": round(gross, 4),
        "fee_gross_ratio": (round(fees / gross, 4) if gross > 0 else None),
        "insufficient": n < 10,
    }


def tercile_labels(vals):
    """Return (lo, hi) thresholds splitting sorted vals into terciles."""
    s = sorted(v for v in vals if v is not None)
    if len(s) < 3:
        return None
    return s[len(s) // 3], s[2 * len(s) // 3]


def fee_accounting(trades, decisions):
    """Per-trade fee / slippage / funding recomputation."""
    per_strategy = defaultdict(lambda: defaultdict(float))
    identity_bad = 0
    fee_split_bad = 0
    funding_rows = []
    for t in trades:
        strat = t["strategy"]
        fees = t["fees_quote"] or 0.0
        gross = t["gross_pnl"]
        pnl = t["pnl_quote"] or 0.0
        # identity check: pnl == gross - fees (when gross recorded)
        if gross is not None and abs((gross - fees) - pnl) > 1e-6:
            identity_bad += 1
        ef, xf = t["entry_fee"], t["exit_fee"]
        if ef is not None and xf is not None and abs(ef + xf - fees) > 1e-6:
            fee_split_bad += 1
        # notional estimate
        if strat == "position_executor" and fees > 0:
            notional = fees / (2 * REPLAY_FEE)  # avg of entry/exit notional
        elif strat == "grid_executor" and fees > 0:
            notional = fees / REPLAY_FEE        # total filled volume (both sides)
        else:
            notional = None
        # config-rate recomputation
        if notional:
            if strat == "grid_executor":
                # notional = total filled volume; each fill charged once
                cfg_fees = notional * MAKER
                slip = notional * SLIP
            else:
                # notional = avg(entry, exit) notional; two sides charged
                cfg_fees = notional * 2 * TAKER
                slip = notional * 2 * SLIP
            per_strategy[strat]["recorded_fees"] += fees
            per_strategy[strat]["cfg_fees"] += cfg_fees
            per_strategy[strat]["notional"] += notional
            per_strategy[strat]["slip_est"] += slip
            per_strategy[strat]["n"] += 1
            per_strategy[strat]["net"] += pnl
            per_strategy[strat]["gross"] += gross if gross is not None \
                else pnl + fees
        # funding estimate for positions
        if strat == "position_executor" and notional:
            dec = decisions.get(t["executor_id"])
            mc = mc_of(dec)
            fr = mc.get("funding_rate")
            if fr is not None and t["ts_open"] and t["ts_close"]:
                hold_h = (t["ts_close"] - t["ts_open"]) / 3600.0
                settlements = hold_h / FUNDING_INTERVAL_H
                signed = fr * notional * settlements   # LONG pays when fr>0
                side = (t["side"] or "").upper()
                if side == "SHORT":
                    signed = -signed
                funding_rows.append({
                    "symbol": t["symbol"], "side": side, "fr": fr,
                    "hold_h": round(hold_h, 2), "cost": signed,
                    "paying": signed > 0,
                })
    return per_strategy, identity_bad, fee_split_bad, funding_rows


def analyze_experiment(con, exp_id):
    trades = load_trades(con, exp_id)
    decisions = load_decisions_by_exec(con, exp_id)
    out = {"n_trades": len(trades)}

    # ---- SEC 1: fee accounting
    per_strategy, identity_bad, fee_split_bad, funding_rows = \
        fee_accounting(trades, decisions)
    out["identity_mismatch"] = identity_bad
    out["fee_split_mismatch"] = fee_split_bad
    out["fee_by_strategy"] = {
        k: {kk: round(vv, 4) for kk, vv in v.items()}
        for k, v in per_strategy.items()}
    if funding_rows:
        out["funding"] = {
            "n": len(funding_rows),
            "net_signed": round(sum(f["cost"] for f in funding_rows), 4),
            "paid_only": round(sum(f["cost"] for f in funding_rows
                                   if f["paying"]), 4),
            "avg_abs_rate": round(sum(abs(f["fr"]) for f in funding_rows)
                                  / len(funding_rows), 6),
            "avg_hold_h": round(sum(f["hold_h"] for f in funding_rows)
                                / len(funding_rows), 2),
        }
    # grid inventory note: REPLAY_END closes settle leftover base at terminal px
    re_end = [t for t in trades if t["close_type"] == "REPLAY_END"]
    out["replay_end_settlements"] = {
        "n": len(re_end),
        "net": round(sum(t["pnl_quote"] for t in re_end), 4),
    }

    # ---- SEC 2: buckets (joined to market context)
    joined = []
    for t in trades:
        dec = decisions.get(t["executor_id"])
        mc = mc_of(dec)
        row = dict(t)
        row["mc"] = mc
        joined.append(row)

    def buckets(key_fn, rows):
        b = defaultdict(list)
        for r in rows:
            b[key_fn(r)].append(r)
        return {k: bucket_stats(v) for k, v in sorted(b.items())}

    pos = [r for r in joined if r["strategy"] == "position_executor"]
    grid = [r for r in joined if r["strategy"] == "grid_executor"]

    out["by_symbol"] = buckets(lambda r: r["symbol"], joined)
    out["by_symbol_pos"] = buckets(lambda r: r["symbol"], pos)
    out["by_symbol_grid"] = buckets(lambda r: r["symbol"], grid)
    out["by_strategy"] = buckets(lambda r: r["strategy"], joined)
    out["by_close_type"] = buckets(lambda r: r["close_type"] or "?", joined)

    def num(r, k):
        v = r["mc"].get(k)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    for feat, label in (("atr_14bar_pct", "atr"), ("atr_pctile_48bar", "atr_pctile"),
                        ("volume_z_48bar", "volz"), ("adx_14bar", "adx")):
        vals = [num(r, feat) for r in pos]
        th = tercile_labels(vals)
        if not th:
            continue
        lo, hi = th

        def lab(r, feat=feat, lo=lo, hi=hi):
            v = num(r, feat)
            if v is None:
                return "missing"
            return "low" if v <= lo else ("mid" if v <= hi else "high")
        out[f"pos_by_{label}"] = {
            "thresholds": [round(lo, 4), round(hi, 4)],
            "buckets": buckets(lab, pos)}
    return out, joined


def main():
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    results = {}
    for exp in (PRIMARY, SECONDARY, FIRST_7D):
        res, _ = analyze_experiment(con, exp)
        results[exp] = res
        print(f"SEC|EXPERIMENT|{exp}|trades={res['n_trades']}|"
              f"identity_bad={res['identity_mismatch']}|"
              f"fee_split_bad={res['fee_split_mismatch']}")
        for strat, agg in res["fee_by_strategy"].items():
            print(f"SEC|FEES|{exp}|{strat}|n={agg['n']}|"
                  f"recorded={agg['recorded_fees']:.4f}|cfg_rate={agg['cfg_fees']:.4f}|"
                  f"slip_est={agg['slip_est']:.4f}|net={agg['net']:.4f}|"
                  f"gross={agg['gross']:.4f}")
        if "funding" in res:
            f = res["funding"]
            print(f"SEC|FUNDING|{exp}|n={f['n']}|net_signed={f['net_signed']}|"
                  f"paid_only={f['paid_only']}|avg_rate={f['avg_abs_rate']}|"
                  f"avg_hold_h={f['avg_hold_h']}")
        re_ = res["replay_end_settlements"]
        print(f"SEC|REPLAY_END|{exp}|n={re_['n']}|net={re_['net']}")
        for sec in ("by_strategy", "by_symbol", "by_symbol_pos", "by_symbol_grid",
                    "by_close_type"):
            for k, v in res[sec].items():
                if v is None:
                    continue
                print(f"SEC|{sec.upper()}|{exp}|{k}|n={v['n']}|net={v['net']}|"
                      f"avg={v['avg']}|wr={v['win_rate']}|fees={v['fees']}|"
                      f"fgr={v['fee_gross_ratio']}"
                      + ("|INSUFFICIENT" if v["insufficient"] else ""))
        for feat in ("atr", "atr_pctile", "volz", "adx"):
            key = f"pos_by_{feat}"
            if key not in res:
                continue
            th = res[key]["thresholds"]
            for k, v in res[key]["buckets"].items():
                if v is None:
                    continue
                print(f"SEC|POS_BY_{feat.upper()}|{exp}|{k}|th={th}|n={v['n']}|"
                      f"net={v['net']}|avg={v['avg']}|wr={v['win_rate']}"
                      + ("|INSUFFICIENT" if v["insufficient"] else ""))

    # ---- live executors
    if LIVE_JSON.exists():
        live = json.loads(LIVE_JSON.read_text(encoding="utf-8"))
        ex = live["data"] if isinstance(live, dict) else live
        live_rows = []
        for e in ex:
            cfg = e.get("config") or {}
            filled = e.get("filled_amount_quote") or 0
            fees = e.get("cum_fees_quote") or 0
            sp = cfg.get("start_price"); ep = cfg.get("end_price")
            width_pct = ((ep - sp) / ((ep + sp) / 2) * 100) if sp and ep else None
            ci = e.get("custom_info") or {}
            held = ci.get("held_position_orders") or []
            live_rows.append({
                "pair": cfg.get("trading_pair"),
                "status": e.get("status"),
                "close_type": e.get("close_type"),
                "net_pnl": e.get("net_pnl_quote"),
                "fees": fees, "filled": filled,
                "fee_rate": (fees / filled if filled else None),
                "spread": cfg.get("min_spread_between_orders"),
                "width_pct": (round(width_pct, 3) if width_pct else None),
                "tp": (cfg.get("triple_barrier_config") or {}).get("take_profit"),
                "amount": cfg.get("total_amount_quote"),
                "held": held,
            })
            print(f"SEC|LIVE|{cfg.get('trading_pair')}|{e.get('status')}|"
                  f"close={e.get('close_type')}|pnl={e.get('net_pnl_quote')}|"
                  f"fees={fees}|filled={filled}|width_pct={live_rows[-1]['width_pct']}|"
                  f"tp={live_rows[-1]['tp']}|held={len(held)}")
        results["live_executors"] = live_rows

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"SEC|WROTE|{OUT}")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
