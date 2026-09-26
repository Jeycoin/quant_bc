"""Smoke test: spawn hummingbot-mcp over stdio, list tools, call one
read-only tool (get_market_data prices on hyperliquid). No LLM involved.

Run:  python scripts/test_mcp.py
"""

import asyncio
import os
import shlex

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    load_dotenv(".env")
    params = StdioServerParameters(
        command=os.getenv("MCP_HUMMINGBOT_COMMAND", "uvx"),
        args=shlex.split(os.getenv("MCP_HUMMINGBOT_ARGS", "")),
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"{len(tools.tools)} tools:")
            for t in tools.tools:
                print(f"  - {t.name}")

            print("\ncalling get_market_data (prices, hyperliquid UBTC-USDC)...")
            result = await session.call_tool(
                "get_market_data",
                {
                    "data_type": "prices",
                    "connector_name": "hyperliquid",
                    "trading_pairs": ["UBTC-USDC"],
                },
            )
            text = "\n".join(getattr(p, "text", str(p)) for p in result.content)
            print(("ERROR: " if result.is_error else "OK: ") + text[:400])


if __name__ == "__main__":
    asyncio.run(main())
