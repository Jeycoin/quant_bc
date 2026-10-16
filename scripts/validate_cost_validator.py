"""Cost Validator + fill-model validation.

For every EXECUTED decision in the baseline replay experiments, recompute the
economics the CostValidator saw (or would have seen) at proposal time and
compare with the realized outcome. Also runs the threshold sensitivity
analysis (slippage / safety margin x0.5 / x1.5) and a realized fee-rate
comparison replay-vs-live.

ASCII-only stdout: SEC| lines. JSON dump to data/analysis/cost_validator.json.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "analytics.db"
OUT = REPO / "data" / "analysis" / "cost_validator.json"
LIVE_JSON = REPO / "data" / "live_executors_snapshot.json"

REPLAY_FEE = 0.0004
MAKER = 0.0002
TAKER = 0.0005
SLIP = 0.0002       # validator applies this ONCE per round trip
SAFETY = 0.0003
FUNDING_INTERVAL_H = 8.0

# replay defaults (backtest_agent.py) — both baselines ran with these
SL_ATR_MULT = 4.0
SL_PCT = 0.01
RR = 2.5
RISK_PER_TRADE = 0.02
LEVERAGE = 1.0
POS_TIME_LIMIT_S = 86400
BAR_SECONDS = 1800
GRID_SIZE = 200.0
GRID_TP = 0.002
GRID_RANGE_PCT = 0.025

EXPERIMENTS = ["exp-f2aafe9b4496", "exp-5bdbd6aa3ceb"]


def position_barriers(atr_pct):
    """Replica of backtest_agent.position_barriers with default args."""
    if atr_pct:
        atr = atr_pct / 100.0
        sl = min(max(SL_ATR_MULT * atr, SL_PCT), 3 * SL_PCT)
        horizon_bars = max(1.0, min(POS_TIME_LIMIT_S / BAR_SECONDS, 48))
        sigma_h = atr * math.sqrt(horizon_bars)
        tp = min(RR * sl, 0.9 * sigma_h)
        tp = max(tp, 1.2 * sl)
        return tp, sl
    return 0.02, SL_PCT


def funding_cost(notional, fr, hold_h, side):
    """CostValidator._funding_cost replica (pay-only)."""
    if fr is None or not side:
        return 0.0
    pays = (side == "LONG" and fr > 0) or (side == "SHORT" and fr < 0)
    if not pays:
        return 0.0
    return notional * abs(fr) * (hold_h / FUNDING_INTERVAL_H)


def predicted_economics(mc, port, side, slip=SLIP, safety=SAFETY):
    """Predicted economics of one position proposal at decision time."""
    atr = mc.get("atr_14bar_pct")
    tp, sl = position_barriers(atr)
    equity = float((port or {}).get("equity") or 10000.0)
    margin = equity * RISK_PER_TRADE / (LEVERAGE * sl)
    margin = min(max(margin, 50.0), 200.0)
    # cross-section same-direction discount + short halving (v0.6)
    open_pos = (port or {}).get("open_positions") or []
    k_same = sum(1 for p in open_pos if p.get("side") == side)
    if k_same:
        margin *= 0.5 ** k_same
    if side == "SHORT":
        margin *= 0.5
    notional = margin * LEVERAGE
    gross = notional * tp
    fees = notional * 2 * TAKER
    slip_cost = notional * slip
    margin_cost = notional * safety
    fund = funding_cost(notional, mc.get("funding_rate"), 6.0, side)
    net = gross - fees - slip_cost - margin_cost - fund
    return {"tp": tp, "sl": sl, "margin": margin, "notional": notional,
            "gross": gross, "fees": fees, "net": net,
            "net_pct": net / notional, "cost_pct": 2 * TAKER + slip + safety,
            "gross_cost_ratio": tp / (2 * TAKER + slip + safety)}


def main():
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    results = {}

    for exp in EXPERIMENTS:
        decisions = {}
        for r in con.execute(
                "SELECT * FROM decision_events WHERE experiment_id=? "
                "AND execution_id IS NOT NULL", (exp,)):
            d = dict(r)
            decisions.setdefault(d["execution_id"], d)
        trades = [dict(r) for r in con.execute(
            "SELECT * FROM trade_events WHERE experiment_id=? AND source='agent'",
            (exp,))]

        pos_rows = []
        grid_rows = []
        for t in trades:
            dec = decisions.get(t["executor_id"])
            if not dec:
                continue
            mc = json.loads(dec.get("market_context") or "{}")
            port = json.loads(dec.get("portfolio_context") or "{}")
            if t["strategy"] == "position_executor":
                pred = predicted_economics(mc, port, (t["side"] or "").upper())
                pos_rows.append({"pnl": t["pnl_quote"], "pred": pred,
                                 "symbol": t["symbol"],
                                 "close_type": t["close_type"]})
            else:
                grid_rows.append({"pnl": t["pnl_quote"],
                                  "gross": t["gross_pnl"],
                                  "fees": t["fees_quote"] or 0.0})

        # ---- position predictions vs realized
        n = len(pos_rows)
        pred_pos = sum(1 for r in pos_rows if r["pred"]["net"] > 0)
        real_pos = sum(1 for r in pos_rows if r["pnl"] > 0)
        # confusion on SIGN of outcome: predicted always positive? then the
        # validator cannot discriminate; measure realized pnl by predicted
        # margin ratio buckets
        ratio_b = defaultdict(list)
        for r in pos_rows:
            ratio = r["pred"]["gross_cost_ratio"]
            lab = ("<5" if ratio < 5 else "5-10" if ratio < 10
                   else "10-20" if ratio < 20 else ">=20")
            ratio_b[lab].append(r["pnl"])
        ratio_out = {k: {"n": len(v), "net": round(sum(v), 4),
                         "wr": round(sum(1 for x in v if x > 0) / len(v), 4)}
                     for k, v in sorted(ratio_b.items())}

        # threshold sensitivity: slippage & safety x0.5 / x1.5
        sens = {}
        for mult in (0.5, 1.0, 1.5):
            rej, rej_pnl = 0, 0.0
            for r in pos_rows:
                p = predicted_economics(
                    {"atr_14bar_pct": None, "funding_rate": None}, {}, "LONG",
                    slip=SLIP * mult, safety=SAFETY * mult)
                # recompute with the trade's own tp: only cost side changes
                notional = r["pred"]["notional"]
                net = (r["pred"]["gross"] - r["pred"]["fees"]
                       - notional * SLIP * mult - notional * SAFETY * mult)
                if net <= 0:
                    rej += 1
                    rej_pnl += r["pnl"]
            sens[str(mult)] = {"would_reject": rej,
                               "rejected_realized_pnl": round(rej_pnl, 4)}

        # what cost multiplier WOULD bind: distribution of net_pct
        net_pcts = sorted(r["pred"]["net_pct"] for r in pos_rows)
        results[exp] = {
            "positions": {
                "n": n,
                "pred_net_positive": pred_pos,
                "realized_positive": real_pos,
                "realized_win_rate": round(real_pos / n, 4) if n else None,
                "pred_net_pct_min": round(net_pcts[0], 6) if net_pcts else None,
                "pred_net_pct_median": round(net_pcts[len(net_pcts) // 2], 6)
                if net_pcts else None,
                "by_gross_cost_ratio": ratio_out,
                "sensitivity": sens,
            },
            "grids": {
                "n": len(grid_rows),
                "net": round(sum(g["pnl"] for g in grid_rows), 4),
                "gross": round(sum(g["gross"] for g in grid_rows
                                   if g["gross"] is not None), 4),
                "fees": round(sum(g["fees"] for g in grid_rows), 4),
                "validator_per_cycle_net_pct": GRID_TP - 2 * MAKER - SLIP - SAFETY,
                "validator_approves": (GRID_TP - 2 * MAKER - SLIP - SAFETY) > 0,
            },
        }
        r = results[exp]
        print(f"SEC|COSTVAL|{exp}|pos_n={n}|pred_pos={pred_pos}|"
              f"real_wr={r['positions']['realized_win_rate']}|"
              f"pred_net_pct_med={r['positions']['pred_net_pct_median']}|"
              f"min={r['positions']['pred_net_pct_min']}")
        for k, v in ratio_out.items():
            print(f"SEC|RATIO|{exp}|{k}|n={v['n']}|net={v['net']}|wr={v['wr']}")
        for k, v in sens.items():
            print(f"SEC|SENS|{exp}|x{k}|would_reject={v['would_reject']}|"
                  f"rej_pnl={v['rejected_realized_pnl']}")
        g = r["grids"]
        print(f"SEC|GRIDVAL|{exp}|n={g['n']}|net={g['net']}|gross={g['gross']}|"
              f"fees={g['fees']}|per_cycle_net_pct={g['validator_per_cycle_net_pct']:.6f}|"
              f"approves={g['validator_approves']}")

    # ---- fill model: replay assumptions vs live realized fee rates
    live = json.loads(LIVE_JSON.read_text(encoding="utf-8"))
    ex = live["data"] if isinstance(live, dict) else live
    live_fees = []
    for e in ex:
        filled = e.get("filled_amount_quote") or 0
        fees = e.get("cum_fees_quote") or 0
        if filled > 0:
            rate = fees / filled
            live_fees.append(rate)
            print(f"SEC|FILL|{(e.get('config') or {}).get('trading_pair')}|"
                  f"{e.get('status')}|fee_rate={rate:.6f}|filled={filled:.2f}|"
                  f"pnl={e.get('net_pnl_quote')}")
    if live_fees:
        avg = sum(live_fees) / len(live_fees)
        print(f"SEC|FILL_SUMMARY|n={len(live_fees)}|avg_fee_rate={avg:.6f}|"
              f"replay_grid_fee={REPLAY_FEE}|replay_pos_fee={REPLAY_FEE}|"
              f"config_maker={MAKER}|config_taker={TAKER}")
        results["live_fill_model"] = {
            "n": len(live_fees), "avg_fee_rate": round(avg, 6),
            "rates": [round(x, 6) for x in live_fees]}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"SEC|WROTE|{OUT}")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
