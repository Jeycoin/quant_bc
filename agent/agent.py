"""AI Trading Manager: LLM + Hummingbot MCP orchestration loop.

Architecture (intentionally thin — Hummingbot does the heavy lifting):

    LLM (Claude / GLM / ...)  <->  this loop  <->  hummingbot-mcp  <->  Hummingbot API

The LLM provider is pluggable (integrations/llm). Every tool call passes
through SafetyGuard before reaching the MCP server. Decisions and outcomes
are recorded in MemoryStore for later review.

The MCP stdio session is kept alive across run() calls (lazy init) so
interactive use does not pay the uvx startup cost on every message.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.memory.store import MemoryStore
from agent.safety import SafetyGuard, SafetyViolation
from integrations.llm import create_llm_client

PROMPT_PATH = Path(__file__).parent / "prompts" / "trading_manager.md"
MAX_TOOL_ROUNDS = 25

_ANALYSIS_BLOCK = re.compile(r"```analysis\s*(\{.*?\})\s*```", re.DOTALL)


def extract_analysis_blocks(text: str) -> list[dict[str, Any]]:
    """Pull structured market-analysis blocks out of a final reply."""
    blocks = []
    for match in _ANALYSIS_BLOCK.finditer(text or ""):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            blocks.append(data)
    return blocks


def _load_system_prompt(mode: str) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return template.replace("{{MODE}}", mode)


class TradingAgent:
    def __init__(self, guard: SafetyGuard, memory: MemoryStore):
        self.guard = guard
        self.memory = memory
        self.llm = create_llm_client()
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._tools: list[dict[str, Any]] | None = None
        self._lock = asyncio.Lock()

    async def _ensure_session(self) -> tuple[ClientSession, list[dict[str, Any]]]:
        async with self._lock:
            if self._session is not None and self._tools is not None:
                return self._session, self._tools
            self._exit_stack = AsyncExitStack()
            params = StdioServerParameters(
                command=os.getenv("MCP_HUMMINGBOT_COMMAND", "uvx"),
                args=shlex.split(os.getenv("MCP_HUMMINGBOT_ARGS", "")),
                env=dict(os.environ),
            )
            read, write = await self._exit_stack.enter_async_context(stdio_client(params))
            session = await self._exit_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            result = await session.list_tools()
            self._tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.input_schema,
                }
                for t in result.tools
            ]
            self._session = session
            return self._session, self._tools

    async def close(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None
            self._tools = None

    async def run(self, user_message: str, history: list[dict[str, Any]] | None = None) -> str:
        session, tools = await self._ensure_session()
        messages = list(history or [])
        messages.append({"role": "user", "content": user_message})
        answer = await self._tool_loop(session, tools, messages)
        for block in extract_analysis_blocks(answer):
            self.memory.record_analysis(
                mode=self.guard.mode,
                symbol=block.get("symbol"),
                regime=block.get("regime"),
                trend=block.get("trend"),
                volatility=block.get("volatility"),
                action=block.get("action"),
                strategy=block.get("strategy"),
                confidence=block.get("confidence"),
                evidence=block.get("evidence"),
                raw=json.dumps(block, ensure_ascii=False),
            )
        return answer

    async def _tool_loop(
        self,
        session: ClientSession,
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> str:
        system = _load_system_prompt(self.guard.mode)
        for _ in range(MAX_TOOL_ROUNDS):
            response = await self.llm.create(system=system, tools=tools, messages=messages)
            messages.append({"role": "assistant", "content": response.blocks})

            if response.stop_reason != "tool_use":
                return response.text

            tool_results = []
            for tool_use in response.tool_uses:
                result_text = await self._call_tool(
                    session, tool_use["name"], tool_use["input"]
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use["id"],
                        "content": result_text,
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        return "Reached the tool-call round limit without a final answer."

    async def _call_tool(
        self, session: ClientSession, name: str, arguments: dict[str, Any]
    ) -> str:
        try:
            self.guard.check_tool_call(name, arguments)
            await self._check_position_capacity(name, arguments)
        except SafetyViolation as exc:
            self.memory.record_decision(
                mode=self.guard.mode,
                market_context="",
                analysis="",
                tool_name=name,
                tool_arguments=arguments,
                outcome=f"BLOCKED: {exc}",
            )
            return f"SAFETY VIOLATION — call rejected: {exc}"

        result = await session.call_tool(name, arguments)
        text = "\n".join(
            getattr(part, "text", str(part)) for part in result.content
        )
        self.memory.record_decision(
            mode=self.guard.mode,
            market_context="",
            analysis="",
            tool_name=name,
            tool_arguments=arguments,
            outcome="ERROR: " + text if result.is_error else "OK",
        )
        return text

    async def _check_position_capacity(self, name: str, arguments: dict[str, Any]) -> None:
        """Enforce max_open_positions before creating a new executor."""
        if name != "manage_executors" or arguments.get("action") != "create":
            return
        max_positions = self.guard.config["safety"].get("max_open_positions")
        if not max_positions:
            return
        running = await self._count_running_executors()
        if running is not None and running >= max_positions:
            raise SafetyViolation(
                f"Open position limit reached: {running} running executors "
                f">= max_open_positions {max_positions}."
            )

    async def _count_running_executors(self) -> int | None:
        """Count RUNNING executors via the Hummingbot API. None = unknown
        (API unreachable) — fail open so a monitoring outage cannot block
        trading entirely; size/connector limits still apply."""
        try:
            from hummingbot_api_client import HummingbotAPIClient

            client = HummingbotAPIClient(
                base_url=os.getenv("HUMMINGBOT_API_URL", "http://127.0.0.1:8100"),
                username=os.getenv("HUMMINGBOT_USERNAME", "admin"),
                password=os.getenv("HUMMINGBOT_PASSWORD", "admin"),
            )
            await client.init()
            try:
                result = await client.executors.search_executors(status="RUNNING")
            finally:
                await client.close()
            if isinstance(result, dict):
                executors = result.get("executors", result.get("data", []))
                total = result.get("total")
                return total if isinstance(total, int) else len(executors)
            if isinstance(result, list):
                return len(result)
            return None
        except Exception:
            return None


async def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")

    load_dotenv()
    guard = SafetyGuard.from_env()
    memory = MemoryStore(
        guard.config["memory"].get("sqlite_path", "data/agent_memory.db")
    )
    agent = TradingAgent(guard, memory)

    print(f"AI Trading Agent — mode: {guard.mode}, model: {agent.llm.model}")
    print("Type a request (e.g. 'scan BTC and ETH'), or 'quit' to exit.")
    history: list[dict[str, Any]] = []
    try:
        while True:
            try:
                user_input = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if user_input.lower() in {"quit", "exit", ""}:
                break
            answer = await agent.run(user_input, history)
            print(f"\n{answer}")
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": answer})
    finally:
        await agent.close()
        memory.close()


if __name__ == "__main__":
    asyncio.run(main())
