"""Grid strategy backtester on historical candles (analytics path, offline).

Simulates the same geometric grid the live grid_executor uses: levels spaced
by the per-level take-profit step, buy when price dips into a level, sell at
level * (1 + tp). Candle-close approximation only — no intrabar fills, flat
taker/maker fee, no funding. It answers "were these grid parameters any good
over this period", NOT "would the exchange have filled us exactly so".

Usage:
  python scripts/backtest_grid.py --symbol BTC --days 30 \
      --start 83000 --end 87000 --amount 300
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

FEE_RATE = 0.0004  # perp taker-ish; conservative flat fee per fill


async def fetch_candles(coin: str, days: int) -> list[dict]:
    """Page 1h candles directly from the Hyperliquid public API.

    The Hummingbot REST candles endpoint has no time-range params (only
    max_records), so history paging goes to the exchange's own API — the
    same venue the live system reads prices from.
    """
    import time

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400_000
    candles: dict[int, dict] = {}
    async with httpx.AsyncClient(
        base_url="https://api.hyperliquid.xyz", timeout=30.0
    ) as client:
        cursor = start_ms
        while cursor < end_ms:
            resp = await client.post("/info", json={
                "type": "candleSnapshot",
                "req": {"coin": coin, "interval": "1h",
                        "startTime": cursor, "endTime": end_ms},
            })
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for c in batch:
                candles[c["t"]] = {
                    "timestamp": c["t"] / 1000,
                    "open": float(c["o"]), "high": float(c["h"]),
                    "low": float(c["l"]), "close": float(c["c"]),
                    "volume": float(c["v"]),
                }
            last = max(c["T"] for c in batch)
            if last <= cursor:
                break
            cursor = last + 1
    return [candles[k] for k in sorted(candles)]


class GridSim:
    """Incremental geometric-grid simulator shared by the batch backtester
    and the agent replay harness. Candle-range touch fills, flat fees."""

    def __init__(self, start: float, end: float, amount: float,
                 tp: float = 0.0002, max_orders: int = 12):
        self.levels: list[float] = []
        p = start
        while p <= end and len(self.levels) < 2000:
            self.levels.append(p)
            p *= 1 + tp
        self.tp = tp
        self.per_level = amount / min(max_orders, len(self.levels))
        self.max_orders = max_orders
        self.cash = amount
        self.bought_at: dict[int, float] = {}  # level idx -> qty held
        self.trades: list[float] = []
        self.fees = 0.0

    def process_candle(self, c: dict) -> None:
        high, low = float(c["high"]), float(c["low"])
        for i in list(self.bought_at):
            sell_price = self.levels[i] * (1 + self.tp)
            if high >= sell_price:
                qty = self.bought_at.pop(i)
                proceeds = qty * sell_price
                sell_fee = proceeds * FEE_RATE
                buy_cost = self.per_level + self.per_level * FEE_RATE
                self.fees += sell_fee
                self.cash += proceeds - sell_fee
                self.trades.append(proceeds - sell_fee - buy_cost)
        for i, level in enumerate(self.levels):
            if i in self.bought_at or len(self.bought_at) >= self.max_orders:
                continue
            if low <= level <= high:
                buy_fee = self.per_level * FEE_RATE
                if self.cash < self.per_level + buy_fee:
                    continue
                self.cash -= self.per_level + buy_fee
                self.fees += buy_fee
                self.bought_at[i] = self.per_level / level

    def equity(self, price: float) -> float:
        return self.cash + sum(qty * price for qty in self.bought_at.values())


def run_grid(candles: list[dict], start: float, end: float, amount: float,
             tp: float = 0.0002, max_orders: int = 12) -> dict:
    sim = GridSim(start, end, amount, tp, max_orders)
    for c in candles:
        sim.process_candle(c)

    last = float(candles[-1]["close"])
    open_value = sum(qty * last for qty in sim.bought_at.values())
    equity = sim.equity(last)
    wins = [t for t in sim.trades if t > 0]
    return {
        "trades": len(sim.trades),
        "wins": len(wins),
        "win_rate": len(wins) / len(sim.trades) if sim.trades else None,
        "grid_pnl": equity - amount,
        "fees": round(sim.fees, 4),
        "open_levels": len(sim.bought_at),
        "open_inventory_value": open_value,
        "buy_hold_pnl": amount * (last / float(candles[0]["close"]) - 1),
        "final_equity": equity,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Grid backtest on historical candles")
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--amount", type=float, default=300.0)
    parser.add_argument("--tp", type=float, default=0.0002)
    args = parser.parse_args()

    pair = f"{args.symbol.upper()}-USD"
    candles = await fetch_candles(args.symbol.upper(), args.days)
    if len(candles) < 24:
        print(f"error: only {len(candles)} candles fetched")
        sys.exit(1)
    result = run_grid(candles, args.start, args.end, args.amount, args.tp)
    first, last = float(candles[0]["close"]), float(candles[-1]["close"])
    print(f"{pair} {args.days}d: {len(candles)} 1h candles, {first:,.0f} -> {last:,.0f}")
    print(f"grid [{args.start:,.0f}–{args.end:,.0f}] ${args.amount:.0f} @tp={args.tp}")
    print(f"  trades={result['trades']} win_rate={result['win_rate'] and round(result['win_rate']*100,1)}%")
    print(f"  grid PnL  = ${result['grid_pnl']:+.4f} (fees ${result['fees']}, open {result['open_levels']} levels worth ${result['open_inventory_value']:.2f})")
    print(f"  buy&hold  = ${result['buy_hold_pnl']:+.4f}")
    print(f"  open inventory value = ${result['open_inventory_value']:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
