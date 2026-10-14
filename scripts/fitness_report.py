"""Composite fitness report for one or more experiments (v1.4).

Usage:
    python scripts/fitness_report.py exp-f2aafe9b4496 [exp-...]

Prints the GT-Score-inspired composite fitness per arm (agent / baseline)
plus its components, so experiment runs can be ranked on more than net PnL.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from analytics.fitness import composite_fitness
from analytics.store import AnalyticsStore


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    store = AnalyticsStore(str(REPO_ROOT / "data" / "analytics.db"))
    try:
        for exp_id in sys.argv[1:]:
            trades = store.list_trade_events(exp_id)
            by_source: dict[str, list[dict]] = {}
            for t in trades:
                by_source.setdefault(t.get("source") or "agent", []).append(t)
            print(f"== {exp_id} ==")
            for src, rows in sorted(by_source.items()):
                fit = composite_fitness(rows)
                comp = fit["components"]
                print(f"  [{src}] fitness={fit['fitness']}")
                print(f"    sharpe={comp['sharpe_like']} "
                      f"ret={comp['total_return_pct']}% "
                      f"maxDD={comp['max_drawdown_pct']}% "
                      f"payoff={comp['payoff']} "
                      f"consistency={comp['window_consistency']} "
                      f"fee_ratio={comp['fee_ratio']}")
                tm = fit["trading"]
                print(f"    trades={tm['closed_count']} "
                      f"pnl={tm['total_pnl_quote']:+.3f} "
                      f"win_rate={tm['win_rate']} "
                      f"PF={tm['profit_factor']}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
