"""End-to-end MVP test: LLM-driven agent run against the real stack.

Runs one analysis request through TradingAgent (GLM -> MCP -> Hummingbot API
-> Hyperliquid) and prints the final answer plus the safety/memory records.

Run:  python scripts/e2e_mvp.py ["your request"]
"""

import asyncio
import sys

from dotenv import load_dotenv

from agent.agent import TradingAgent
from agent.memory.store import MemoryStore
from agent.safety import SafetyGuard

REQUEST = (
    "请完成一次市场扫描：1) 获取 Hyperliquid 上 BTC 和 ETH 的最新价格；"
    "2) 获取 BTC-USD 永续合约的资金费率；3) 获取 BTC 最近几小时的 K线；"
    "4) 基于以上数据分析当前市场状态（趋势/震荡/波动），并说明现在是否"
    "存在交易机会、如果交易你会选择 Hummingbot 的哪类策略。"
    "注意：当前是 PAPER 模式，本次只做分析，不要创建任何执行器或订单。"
)


async def main() -> None:
    load_dotenv(".env")
    request = sys.argv[1] if len(sys.argv) > 1 else REQUEST
    guard = SafetyGuard.from_env()
    memory = MemoryStore(guard.config["memory"].get("sqlite_path", "data/agent_memory.db"))
    agent = TradingAgent(guard, memory)

    print(f"mode={guard.mode} model={agent.llm.model}")
    print(f"request: {request}\n")
    try:
        answer = await agent.run(request)
    finally:
        await agent.close()
    print("=" * 60)
    print(answer)
    print("=" * 60)

    decisions = memory.recent_decisions(30)
    print(f"\nmemory: {len(decisions)} tool call(s) recorded")
    for d in decisions[:10]:
        print(f"  [{d['outcome'][:20]}] {d['tool_name']} {str(d['tool_arguments'])[:80]}")
    memory.close()


if __name__ == "__main__":
    asyncio.run(main())
