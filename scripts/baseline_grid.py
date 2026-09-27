"""Baseline runner: fixed grid strategy WITHOUT the agent (master prompt §14).

Creates a grid executor directly through the Hummingbot API and tags it
source='baseline' in analytics, so AI-assisted runs can be compared against
a no-AI fixed strategy under the same market conditions.

The same hard limits as the agent safety layer are enforced inline (testnet
connector only, quote amount cap) — a baseline run is still a trade.

Usage:
  python scripts/baseline_grid.py --pair BTC-USDT --amount 200 \
      --start 83000 --end 85000 [--experiment <id>]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Baseline grid executor (no AI)")
    parser.add_argument("--pair", required=True, help="e.g. BTC-USDT")
    parser.add_argument("--amount", type=float, required=True, help="quote amount")
    parser.add_argument("--start", type=float, required=True, help="grid start price")
    parser.add_argument("--end", type=float, required=True, help="grid end price")
    parser.add_argument("--connector", default="binance_perpetual_testnet")
    parser.add_argument("--experiment", default=None, help="experiment_id tag")
    args = parser.parse_args()

    config = yaml.safe_load((REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    safety = config["safety"]

    if os.getenv("LIVE_TRADING", "false").strip().lower() != "true":
        patterns = safety["paper_connector_patterns"]
        if not any(p in args.connector for p in patterns):
            print(f"error: connector {args.connector!r} is not a paper/testnet connector")
            sys.exit(1)
    if args.amount > safety["max_order_quote_amount"]:
        print(f"error: amount {args.amount} exceeds max_order_quote_amount "
              f"{safety['max_order_quote_amount']}")
        sys.exit(1)

    base_url = os.getenv("HUMMINGBOT_API_URL", "http://127.0.0.1:8100").rstrip("/")
    auth = (os.getenv("HUMMINGBOT_USERNAME", "admin"),
            os.getenv("HUMMINGBOT_PASSWORD", "admin"))

    executor_config = {
        "type": "grid_executor",
        "connector_name": args.connector,
        "trading_pair": args.pair,
        "total_amount_quote": args.amount,
        "start_price": args.start,
        "end_price": args.end,
        "limit_price": args.start,
        "side": 1,  # BUY side grid
        "min_spread_between_orders": 0.0001,
        "min_order_amount_quote": max(10.0, args.amount / 10),
        "max_open_orders": 12,
        "activation_bounds": 0.001,
        "order_frequency": 5,
        "max_orders_per_batch": 1,
        "keep_position": False,
        "coerce_tp_to_step": True,
        "triple_barrier_config": {
            "open_order_type": 3,        # LIMIT
            "take_profit": 0.0002,
            "take_profit_order_type": 3,  # LIMIT
        },
    }

    async with httpx.AsyncClient(base_url=base_url, auth=auth, timeout=30.0) as client:
        resp = await client.post("/executors/", json={"executor_config": executor_config})
        resp.raise_for_status()
        result = resp.json()
    executor_id = result.get("executor_id") or result.get("id") or str(result)
    print(f"baseline grid executor created: {executor_id}")

    from analytics.store import AnalyticsStore

    store = AnalyticsStore(str(REPO_ROOT / "data" / "analytics.db"))
    try:
        store.upsert_trade_event(
            executor_id=str(executor_id),
            symbol=args.pair,
            strategy="grid_executor",
            connector=args.connector,
            status="RUNNING",
            source="baseline",
            experiment_id=args.experiment,
        )
    finally:
        store.close()
    print(f"tagged source=baseline"
          + (f" experiment={args.experiment}" if args.experiment else ""))


if __name__ == "__main__":
    asyncio.run(main())
