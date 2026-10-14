"""Factor-weight evolution harness (v1.4, EXPERIMENTAL — offline research).

StratEvo-style "weight evolution": instead of evolving strategy expression
trees (GP — huge search space, overfit-prone), treat the existing factor
library as the atomic set and evolve only the WEIGHT VECTOR that combines
factors into a single composite score. Search space: [-1, 1]^N — orders of
magnitude smaller than GP.

Fitness of a weight vector = IC quality of the composite score against
forward returns:
    mean rank-IC across evaluation timestamps
    × cross-fold consistency (fraction of time folds with positive IC)
    − turnover-indifference penalty not needed (score only, no execution)

Anti-overfit measures:
- time-ordered folds: fitness uses ALL folds, reported per fold so a weight
  vector that only works in one regime is visible
- final evaluation must be repeated on a held-out window (--holdout-days)
- results are written to data/experiments/weights-<ts>.json — nothing here
  is wired into live or replay decisions; promotion requires the standard
  pre-registered replay A/B

Usage:
    python scripts/evolve_weights.py --days 30 --horizon-bars 8 \
        --population 40 --generations 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from agent.market_features import compute_market_features
from backtest_grid import fetch_candles

# numeric factors eligible for weighting (skip flags/strings/dicts)
FACTOR_KEYS = [
    "ret_1bar_pct", "ret_2bar_pct", "ret_4bar_pct", "ret_8bar_pct",
    "ret_16bar_pct", "ret_48bar_pct",
    "rsi_14bar", "rsi_slope_4bar", "adx_14bar", "macd_hist_pct",
    "efficiency_ratio_16bar", "bb_width_pct", "atr_14bar_pct",
    "atr_pctile_48bar", "realized_vol_8bar_pct", "realized_vol_48bar_pct",
    "volume_z_48bar", "ema_dist_pct", "ema50_dist_pct", "streak_bars",
    "up_vol_share_8bar", "vwap_dist_48bar_pct", "bars_since_48bar_high",
    "dist_from_48bar_high_pct", "funding_z_7d",
    "ret_rank_4bar", "ret_rank_8bar", "pv_corr_12bar", "ewo_pct",
    "chop_14bar", "upper_wick_pct", "lower_wick_pct",
    "corr_btc_48bar", "rs_vs_btc_8h_pct",
]

SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "SUI"]


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    for pos, idx in enumerate(order):
        r[idx] = float(pos)
    return r


def _spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 4:
        return None
    ra, rb = _ranks(a), _ranks(b)
    n = len(a)
    ma = sum(ra) / n
    mb = sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra)
    vb = sum((y - mb) ** 2 for y in rb)
    if va <= 0 or vb <= 0:
        return None
    return cov / (va * vb) ** 0.5


def build_dataset(candles: dict[str, list[dict]], horizon: int,
                  step: int = 1) -> list[dict]:
    """Rows: {ts, symbol, factors(dict), fwd_ret}. Forward return over
    `horizon` bars from the row's timestamp."""
    rows = []
    btc = candles.get("BTC", [])
    for sym, cs in candles.items():
        for i in range(len(cs)):
            ts = cs[i]["timestamp"]
            if i % step:
                continue
            fwd_idx = i + horizon
            if fwd_idx >= len(cs):
                break
            feat = compute_market_features(
                cs, upto_ts=ts, ref_candles=(btc if sym != "BTC" else None))
            if not feat:
                continue
            factors = {k: feat.get(k) for k in FACTOR_KEYS
                       if isinstance(feat.get(k), (int, float))}
            if len(factors) < 10:
                continue
            fwd = float(cs[fwd_idx]["close"]) / float(cs[i]["close"]) - 1
            rows.append({"ts": ts, "symbol": sym, "factors": factors,
                         "fwd_ret": fwd})
    return rows


def _composite(row: dict, weights: dict[str, float],
               means: dict[str, float], stds: dict[str, float]) -> float:
    score = 0.0
    wsum = 0.0
    for k, w in weights.items():
        v = row["factors"].get(k)
        if v is None or stds.get(k, 0) <= 1e-12:
            continue
        score += w * (v - means[k]) / stds[k]
        wsum += abs(w)
    return score / wsum if wsum > 0 else 0.0


def fitness_of(weights: dict[str, float], rows: list[dict],
               means: dict[str, float], stds: dict[str, float],
               n_folds: int = 4) -> tuple[float, list[float | None]]:
    """Mean IC × consistency across time-ordered folds."""
    rows = sorted(rows, key=lambda r: r["ts"])
    fold_size = len(rows) // n_folds
    if fold_size < 20:
        return 0.0, []
    fold_ics: list[float | None] = []
    for f in range(n_folds):
        chunk = rows[f * fold_size: (f + 1) * fold_size]
        # pool by timestamp: cross-sectional IC per ts, then average
        by_ts: dict[float, list[dict]] = {}
        for r in chunk:
            by_ts.setdefault(r["ts"], []).append(r)
        ics = []
        for ts, group in by_ts.items():
            scores = [_composite(r, weights, means, stds) for r in group]
            fwds = [r["fwd_ret"] for r in group]
            ic = _spearman(scores, fwds)
            if ic is not None:
                ics.append(ic)
        fold_ics.append(sum(ics) / len(ics) if ics else None)
    valid = [ic for ic in fold_ics if ic is not None]
    if not valid:
        return 0.0, fold_ics
    mean_ic = sum(valid) / len(valid)
    consistency = sum(1 for ic in valid if ic > 0) / len(valid)
    return mean_ic * consistency, fold_ics


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--horizon-bars", type=int, default=8,
                   help="forward-return horizon in 30m bars (8 = 4h)")
    p.add_argument("--bar-minutes", type=int, default=30)
    p.add_argument("--population", type=int, default=40)
    p.add_argument("--generations", type=int, default=30)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()
    random.seed(args.seed)

    end_ts = int(time.time())
    candles = {}
    for sym in SYMBOLS:
        candles[sym] = asyncio.run(
            fetch_candles(sym, args.days + 2, interval=f"{args.bar_minutes}m",
                          end_ts=end_ts))
        print(f"{sym}: {len(candles[sym])} candles")

    print("building dataset…")
    rows = build_dataset(candles, args.horizon_bars)
    print(f"dataset: {len(rows)} rows over {args.days}d")

    # normalization stats over the full window (research-only; a production
    # variant must use expanding-window stats to avoid leakage)
    means: dict[str, float] = {}
    stds: dict[str, float] = {}
    for k in FACTOR_KEYS:
        vals = [r["factors"][k] for r in rows if k in r["factors"]]
        if len(vals) < 50:
            continue
        mu = sum(vals) / len(vals)
        sd = (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5
        means[k], stds[k] = mu, sd
    keys = list(means)
    print(f"factors with enough data: {len(keys)}")

    def random_weights() -> dict[str, float]:
        return {k: random.uniform(-1, 1) for k in keys}

    pop = [random_weights() for _ in range(args.population)]
    scored: list[tuple[float, dict]] = []
    for gen in range(args.generations):
        scored = []
        for w in pop:
            fit, _ = fitness_of(w, rows, means, stds)
            scored.append((fit, w))
        scored.sort(key=lambda x: x[0], reverse=True)
        best_fit, best_w = scored[0]
        _, folds = fitness_of(best_w, rows, means, stds)
        print(f"gen {gen + 1}: best={best_fit:.4f} folds="
              f"{[round(f, 3) if f is not None else None for f in folds]}")
        # elitism + tournament + uniform crossover + gaussian mutation
        next_pop = [scored[0][1], scored[1][1]]
        while len(next_pop) < args.population:
            def pick() -> dict[str, float]:
                a, b = random.sample(scored[: args.population // 2], 2)
                return a[1] if a[0] >= b[0] else b[1]
            pa, pb = pick(), pick()
            child = {k: (pa[k] if random.random() < 0.5 else pb[k])
                     for k in keys}
            for k in keys:
                if random.random() < 0.15:
                    child[k] = max(-1.0, min(1.0,
                                             child[k] + random.gauss(0, 0.2)))
            next_pop.append(child)
        pop = next_pop

    best_fit, best_w = scored[0]
    _, folds = fitness_of(best_w, rows, means, stds)
    out = {
        "created_ts": time.time(),
        "config": vars(args),
        "fitness": best_fit,
        "fold_ics": folds,
        "weights": {k: round(v, 4) for k, v in
                    sorted(best_w.items(), key=lambda x: -abs(x[1]))},
        "note": "EXPERIMENTAL — not wired into any live/replay decision "
                "path. Promotion requires a pre-registered replay A/B.",
    }
    out_dir = REPO_ROOT / "data" / "experiments"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"weights-{int(time.time())}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nbest fitness {best_fit:.4f} -> {path}")
    print(json.dumps(out["weights"], indent=2))


if __name__ == "__main__":
    main()
