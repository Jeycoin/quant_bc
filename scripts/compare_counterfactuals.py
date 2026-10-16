"""Compare counterfactual experiment runs against their source baselines.

Finds every bt-counterfactual-* experiment derived from the given source
ids, recomputes headline economics from trade_events, and prints a table.
Usage: python scripts/compare_counterfactuals.py [src_exp ...]
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "analytics.db"

SOURCES = ["exp-f2aafe9b4496", "exp-5bdbd6aa3ceb"]


def metrics(con, exp_id):
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM trade_events WHERE experiment_id=? AND source='agent'",
        (exp_id,))]
    n = len(rows)
    net = sum(r["pnl_quote"] or 0 for r in rows)
    fees = sum(r["fees_quote"] or 0 for r in rows)
    gross = sum((r["gross_pnl"] if r["gross_pnl"] is not None
                 else (r["pnl_quote"] or 0) + (r["fees_quote"] or 0))
                for r in rows)
    wins = [r["pnl_quote"] for r in rows if (r["pnl_quote"] or 0) > 0]
    losses = [r["pnl_quote"] for r in rows if (r["pnl_quote"] or 0) <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) else None
    grid = [r for r in rows if r["strategy"] == "grid_executor"]
    pos = [r for r in rows if r["strategy"] == "position_executor"]
    exp = con.execute("SELECT summary_json FROM experiments WHERE experiment_id=?",
                      (exp_id,)).fetchone()
    dd = None
    if exp and exp[0]:
        s = json.loads(exp[0])
        dd = (s.get("agent_equity_metrics") or {}).get("max_drawdown_pct")
    return {
        "n": n, "n_grid": len(grid), "n_pos": len(pos),
        "net": round(net, 2), "gross": round(gross, 2),
        "fees": round(fees, 2),
        "fgr": (round(fees / gross, 2) if gross > 0 else None),
        "pf": (round(pf, 2) if pf else None),
        "wr": (round(len(wins) / n, 3) if n else None),
        "maxdd": (round(dd, 2) if dd is not None else None),
        "grid_net": round(sum(r["pnl_quote"] or 0 for r in grid), 2),
        "pos_net": round(sum(r["pnl_quote"] or 0 for r in pos), 2),
    }


def label(notes: str) -> str:
    if "sym_cd_h=6" in notes:
        return "(a) turnover cap: 6h symbol cooldown"
    if "grid_range=0.0375" in notes:
        return "(b) grid width x1.5 (range 3.75%)"
    if "grid_tp=0.004" in notes:
        return "(b2) grid spacing x2 (tp 0.4%)"
    if "grid_adx_max=25" in notes:
        return "(c) trend filter: no new grid when ADX>25"
    return "control (current gates, no override)"


def main():
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    sources = sys.argv[1:] or SOURCES
    for src in sources:
        # baseline itself
        bm = metrics(con, src)
        print(f"SEC|BASELINE|{src}|n={bm['n']}|net={bm['net']}|gross={bm['gross']}|"
              f"fees={bm['fees']}|fgr={bm['fgr']}|pf={bm['pf']}|wr={bm['wr']}|"
              f"maxdd={bm['maxdd']}|grid_net={bm['grid_net']}|pos_net={bm['pos_net']}")
        rows = con.execute(
            "SELECT experiment_id, notes, started_at FROM experiments "
            "WHERE name=? AND status='completed' ORDER BY started_at",
            (f"bt-counterfactual-{src}",)).fetchall()
        for r in rows:
            m = metrics(con, r["experiment_id"])
            lab = label(r["notes"] or "")
            print(f"SEC|CF|{src}|{r['experiment_id']}|{lab}|n={m['n']}|"
                  f"net={m['net']}|gross={m['gross']}|fees={m['fees']}|"
                  f"fgr={m['fgr']}|pf={m['pf']}|wr={m['wr']}|maxdd={m['maxdd']}|"
                  f"grid_net={m['grid_net']}|pos_net={m['pos_net']}|"
                  f"n_grid={m['n_grid']}|n_pos={m['n_pos']}")
    con.close()


if __name__ == "__main__":
    main()
