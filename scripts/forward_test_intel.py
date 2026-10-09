"""Intelligence signal forward-looking test (v1.1 Phase B gate).

For each intelligence signal kind, compute Spearman rank IC between the signal
value and the symbol's future return at 1h / 4h / 24h horizons.

A signal only earns its way into the LLM prompt if it shows forward-looking
correlation out of sample. Small samples are reported with explicit caveats.

Usage: python scripts/forward_test_intel.py
"""
import json
import math
import sqlite3
import urllib.request
from datetime import datetime, timezone

DB = "data/intelligence.db"
SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "SUI"]
HORIZONS_H = [1, 4, 24]


def fetch_candles(coin, start_ts, end_ts, interval="1h"):
    body = json.dumps({
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval,
                "startTime": int(start_ts * 1000), "endTime": int(end_ts * 1000)},
    }).encode()
    req = urllib.request.Request(
        "https://api.hyperliquid.xyz/info", data=body,
        headers={"Content-Type": "application/json"})
    data = json.loads(urllib.request.urlopen(req, timeout=60).read())
    return {c["t"] / 1000: float(c["c"]) for c in data}


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(x, y):
    if len(x) < 8:
        return None
    rx, ry = ranks(x), ranks(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return cov / math.sqrt(vx * vy)


def fwd_ret(prices, ts, hours):
    p0 = prices.get(ts)
    p1 = prices.get(ts + hours * 3600)
    if p0 is None or p1 is None or p0 == 0:
        return None
    return (p1 - p0) / p0


def main():
    conn = sqlite3.connect(DB)
    lo, hi = conn.execute("SELECT MIN(ts), MAX(ts) FROM intel_signals").fetchone()
    print(f"signal window: {datetime.fromtimestamp(lo, timezone.utc)} -> {datetime.fromtimestamp(hi, timezone.utc)}")

    candles = {}
    for s in SYMBOLS:
        candles[s] = fetch_candles(s, lo - 3600, hi + 26 * 3600)

    def price_at(sym, ts):
        # nearest 1h close at or before ts
        c = candles.get(sym, {})
        ks = [k for k in c if k <= ts]
        return c[max(ks)] if ks else None

    def fwd(sym, ts, hours):
        p0 = price_at(sym, ts)
        p1 = price_at(sym, ts + hours * 3600)
        if p0 is None or p1 is None or p0 == 0:
            return None
        return (p1 - p0) / p0

    report = {}

    # --- per-symbol signals: funding_rate, open_interest ---
    for kind in ["funding_rate", "open_interest"]:
        rows = conn.execute(
            "SELECT ts, symbol, value FROM intel_signals WHERE kind=? ORDER BY ts", (kind,)).fetchall()
        by_sym = {}
        for ts, sym, val in rows:
            by_sym.setdefault(sym, []).append((ts, val))
        for sym, series in by_sym.items():
            # OI uses pct change vs previous observation; funding uses raw value
            sigs = []
            for i, (ts, val) in enumerate(series):
                if kind == "open_interest":
                    if i == 0 or series[i - 1][1] == 0:
                        continue
                    sig = (val - series[i - 1][1]) / series[i - 1][1]
                else:
                    sig = val
                sigs.append((ts, sig))
            for h in HORIZONS_H:
                xs, ys = [], []
                for ts, sig in sigs:
                    r = fwd(sym, ts, h)
                    if r is not None:
                        xs.append(sig)
                        ys.append(r)
                ic = spearman(xs, ys)
                if ic is not None:
                    report.setdefault(kind, {}).setdefault(sym, {})[f"{h}h"] = (round(ic, 3), len(xs))

    # --- global signals vs BTC ---
    for kind in ["fear_greed", "sentiment_change", "network_activity"]:
        rows = conn.execute(
            "SELECT ts, value, payload FROM intel_signals WHERE kind=? ORDER BY ts", (kind,)).fetchall()
        for h in HORIZONS_H:
            xs, ys = [], []
            for ts, val, payload in rows:
                if kind == "network_activity":
                    try:
                        val = json.loads(payload).get("change_pct_1d")
                    except Exception:
                        val = None
                    if val is None:
                        continue
                r = fwd("BTC", ts, h)
                if r is not None:
                    xs.append(val)
                    ys.append(r)
            ic = spearman(xs, ys)
            if ic is not None:
                report.setdefault(kind, {}).setdefault("BTC", {})[f"{h}h"] = (round(ic, 3), len(xs))

    # --- narratives: strength vs future return ---
    nrows = conn.execute(
        "SELECT ts, symbol, strength FROM narratives ORDER BY ts").fetchall()
    for h in HORIZONS_H:
        xs, ys = [], []
        for ts, sym, strength in nrows:
            if sym not in SYMBOLS:
                continue
            r = fwd(sym, ts, h)
            if r is not None:
                xs.append(strength)
                ys.append(r)
        ic = spearman(xs, ys)
        if ic is not None:
            report.setdefault("narrative_strength", {}).setdefault("ALL", {})[f"{h}h"] = (round(ic, 3), len(xs))

    print("\n=== Spearman IC (signal vs future return) ===")
    for kind, syms in report.items():
        print(f"\n[{kind}]")
        for sym, horizons in syms.items():
            cells = "  ".join(f"{h}: IC={ic:+.3f} (n={n})" for h, (ic, n) in horizons.items())
            print(f"  {sym:5s} {cells}")

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signal_window": {"start": lo, "end": hi},
        "note": "Spearman rank IC; n<30 unreliable; |IC|<0.1 ~ no signal",
        "results": {k: {s: {h: {"ic": ic, "n": n} for h, (ic, n) in hz.items()}
                        for s, hz in syms.items()} for k, syms in report.items()},
    }
    with open("data/forward_test_intel.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\nsaved -> data/forward_test_intel.json")


if __name__ == "__main__":
    main()
