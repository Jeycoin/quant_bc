"""Verify the paper-trading path through the Hummingbot API.

Verified finding (2026-09): the API does NOT expose *_paper_trade
connectors. The paper path for agent-driven trading is exchange TESTNET
connectors (binance_perpetual_testnet, hyperliquid_perpetual_testnet, ...).

This script checks, read-only:
  1. API connectivity
  2. which testnet connectors are available (our PAPER venues)
  3. public market data on a live connector (binance spot candles)
  4. public market data on a testnet connector (no keys needed for reads)

Run:  python scripts/verify_paper.py
Requires: hummingbot API running on HUMMINGBOT_API_URL
"""

import asyncio
import os

from dotenv import load_dotenv
from hummingbot_api_client import HummingbotAPIClient


async def main() -> None:
    load_dotenv()
    client = HummingbotAPIClient(
        base_url=os.getenv("HUMMINGBOT_API_URL", "http://localhost:8000"),
        username=os.getenv("HUMMINGBOT_USERNAME", "admin"),
        password=os.getenv("HUMMINGBOT_PASSWORD", "admin"),
    )

    print("== 1. API connectivity ==")
    try:
        await client.init()
        connectors = await client.connectors.list_connectors()
    except Exception as exc:
        print(f"FAILED: cannot reach API — {exc}")
        return
    print(f"OK, {len(connectors)} connectors")

    print("\n== 2. paper venues (testnet connectors) ==")
    testnets = [c for c in connectors if "testnet" in str(c)]
    paper = [c for c in connectors if "paper_trade" in str(c)]
    print("testnet:", testnets or "NONE")
    print("paper_trade:", paper or "NONE (expected — not exposed by the API)")

    print("\n== 3. public market data: hyperliquid spot UBTC-USDC candles ==")
    print("(binance/okx/bybit are blocked from this network; hyperliquid + gate_io are direct)")
    try:
        candles = await client.market_data.get_candles(
            connector_name="hyperliquid",
            trading_pair="UBTC-USDC",
            interval="1m",
            max_records=3,
        )
        print("OK:", str(candles)[:300])
    except Exception as exc:
        print(f"FAILED: {exc}")

    print("\n== 4. hyperliquid_perpetual BTC-USD: candles + funding ==")
    try:
        candles = await client.market_data.get_candles(
            connector_name="hyperliquid_perpetual",
            trading_pair="BTC-USD",
            interval="1m",
            max_records=3,
        )
        print("candles OK:", str(candles)[:200])
    except Exception as exc:
        print(f"candles FAILED: {exc}")
    try:
        funding = await client.market_data.get_funding_info(
            connector_name="hyperliquid_perpetual",
            trading_pair="BTC-USD",
        )
        print("funding OK:", str(funding)[:300])
    except Exception as exc:
        print(f"funding FAILED: {exc}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
