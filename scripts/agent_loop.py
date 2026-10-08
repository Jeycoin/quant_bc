"""Autonomous agent loop for long-running testnet experiments (Phase 9/10).

Runs the trading agent on a fixed interval so a multi-day experiment does
not depend on an interactive session. Every Nth cycle includes a trade
review request. All output goes to data/agent_loop.log.

Safety: the agent still passes every tool call through SafetyGuard — paper
connectors only, amount/position caps apply. This script changes nothing
about the safety boundary; it only schedules prompts.

Usage:
  python scripts/agent_loop.py [--interval-min 30] [--review-every 6]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.agent import TradingAgent
from agent.memory.store import MemoryStore
from agent.safety import SafetyGuard

SCAN_REQUEST = (
    "执行一次例行市场扫描与交易管理：\n"
    "1) 用 get_market_data 获取 BTC、ETH、SOL、XRP、SUI 的价格、K线、资金费率；\n"
    "2) 用 get_portfolio_overview 查看账户与持仓；\n"
    "3) 判断每个币种的 Market Regime 和是否存在交易机会；\n"
    "4) 若存在高置信度机会且风险允许，选择合适的已有策略创建执行器；"
    "没有机会就保持 WAIT——不交易是完全合理的决定；\n"
    "5) 最后为每个分析的币种输出 ```analysis 结构化块。"
)

REVIEW_REQUEST = (
    "复盘当前所有已终止执行器的交易结果：\n"
    "1) 用 manage_executors (action=search) 找到最近终止的执行器；\n"
    "2) 分析每笔交易：当时的判断是什么、结果如何、哪里对哪里错；\n"
    "3) 把复盘结论告诉我（会写入 Memory 供未来参考）；\n"
    "4) 照常完成市场扫描并输出 ```analysis 结构化块。"
)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous agent loop")
    parser.add_argument("--interval-min", type=float, default=30.0)
    parser.add_argument("--review-every", type=int, default=6)
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    load_dotenv(REPO_ROOT / ".env")
    log_path = REPO_ROOT / "data" / "agent_loop.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("agent_loop")

    guard = SafetyGuard.from_env(str(REPO_ROOT / "config" / "settings.yaml"))
    memory = MemoryStore(
        str(REPO_ROOT / guard.config["memory"].get("sqlite_path", "data/agent_memory.db"))
    )
    agent = TradingAgent(guard, memory)
    log.info("agent loop started: mode=%s model=%s interval=%.1fmin review-every=%d",
             guard.mode, agent.llm.model, args.interval_min, args.review_every)

    cycle = 0
    try:
        while True:
            cycle += 1
            request = REVIEW_REQUEST if cycle % args.review_every == 0 else SCAN_REQUEST
            started = time.time()
            try:
                answer = await agent.run(request)
                log.info("cycle %d done in %.0fs: %s", cycle, time.time() - started,
                         answer[:300].replace("\n", " "))
            except Exception as exc:
                # One failed cycle must not kill a multi-day experiment.
                log.exception("cycle %d failed: %s", cycle, exc)
                try:
                    await agent.close()  # drop possibly-broken MCP session
                except Exception:
                    pass
            await asyncio.sleep(args.interval_min * 60)
    finally:
        await agent.close()
        memory.close()


if __name__ == "__main__":
    asyncio.run(main())
