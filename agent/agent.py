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
from analytics.store import AnalyticsStore, new_decision_id
from analytics.versioning import current_versions
from integrations.llm import create_llm_client

PROMPT_PATH = Path(__file__).parent / "prompts" / "trading_manager.md"
MAX_TOOL_ROUNDS = 25

_ANALYSIS_BLOCK = re.compile(r"```analysis\s*(\{.*?\})\s*```", re.DOTALL)
_REVIEW_BLOCK = re.compile(r"```review\s*(\{.*?\})\s*```", re.DOTALL)
_EXECUTOR_ID = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,48}")

_STANDARD_REGIMES = {
    "TRENDING_BULL", "TRENDING_BEAR", "RANGING", "HIGH_VOLATILITY",
    "LOW_VOLATILITY", "BREAKOUT", "UNCERTAIN",
}
_REGIME_ALIASES = {
    "TRENDING_UP": "TRENDING_BULL",
    "TRENDING_DOWN": "TRENDING_BEAR",
    "BULL": "TRENDING_BULL",
    "BEAR": "TRENDING_BEAR",
    "VOLATILE": "HIGH_VOLATILITY",
}


def normalize_regime(value: Any) -> str | None:
    """Map free-form LLM regime labels onto the standard vocabulary."""
    if not value:
        return None
    v = str(value).strip().upper().replace(" ", "_").replace("-", "_")
    v = _REGIME_ALIASES.get(v, v)
    return v if v in _STANDARD_REGIMES else "UNCERTAIN"


def extract_executor_id(result_text: str) -> str | None:
    """Pull the executor id out of a manage_executors/create MCP result."""
    try:
        data = json.loads(result_text)
        if isinstance(data, dict) and data.get("executor_id"):
            return str(data["executor_id"])
    except (json.JSONDecodeError, TypeError):
        pass
    match = _EXECUTOR_ID.search(result_text or "")
    return match.group(0) if match else None


def extract_analysis_blocks(text: str) -> list[dict[str, Any]]:
    """Pull structured market-analysis blocks out of a final reply."""
    return _extract_blocks(_ANALYSIS_BLOCK, text)


def extract_review_blocks(text: str) -> list[dict[str, Any]]:
    """Pull structured trade-review blocks out of a final reply."""
    return _extract_blocks(_REVIEW_BLOCK, text)


def _extract_blocks(pattern: re.Pattern[str], text: str) -> list[dict[str, Any]]:
    blocks = []
    for match in pattern.finditer(text or ""):
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
        self.versions = current_versions()
        # Analytics path (AI Quant Lab). Best-effort: if the analytics DB
        # cannot be opened the agent keeps trading without it.
        try:
            self.analytics: AnalyticsStore | None = AnalyticsStore(
                os.getenv("ANALYTICS_DB", "data/analytics.db")
            )
        except Exception:
            self.analytics = None
        # Per-run capture state, reset at the start of each run().
        self._created_executors: list[str] = []
        self._blocked = False
        self._market_snapshot: dict[str, Any] = {}
        # Set when a memory write fails — degraded mode: no new trades
        # (master prompt §28: never trade freely when memory is down).
        self._memory_broken = False

    def _safe_record(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Memory writes are best-effort; a failure flips degraded mode."""
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            self._memory_broken = True
            print(f"[warn] memory write failed, degraded mode on: {exc}",
                  file=sys.stderr)
            return None

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
        if self.analytics is not None:
            self.analytics.close()

    async def run(self, user_message: str, history: list[dict[str, Any]] | None = None) -> str:
        session, tools = await self._ensure_session()
        decision_id = new_decision_id()
        self._created_executors = []
        self._blocked = False
        self._market_snapshot = {}
        portfolio_ctx = await self._snapshot_portfolio()
        messages = list(history or [])
        messages.append({"role": "user", "content": user_message})
        answer = await self._tool_loop(session, tools, messages)
        for block in extract_analysis_blocks(answer):
            self._safe_record(
                self.memory.record_analysis,
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
        review_blocks = extract_review_blocks(answer)
        if review_blocks:
            self._safe_record(
                self.memory.record_review,
                subject=review_blocks[0].get("execution_id") or "review",
                review=answer,
            )
            if self.analytics is not None:
                for block in review_blocks:
                    try:
                        self.analytics.record_review_event(
                            decision_id=decision_id,
                            execution_id=block.get("execution_id"),
                            outcome=block.get("outcome"),
                            decision_quality=block.get("decision_quality"),
                            execution_quality=block.get("execution_quality"),
                            regime_accuracy=block.get("regime_accuracy"),
                            main_error=block.get("main_error"),
                            main_success=block.get("main_success"),
                            lesson=block.get("lesson"),
                            raw=json.dumps(block, ensure_ascii=False),
                        )
                    except Exception:
                        pass  # analytics path must never break trading
        self._record_decision_events(decision_id, answer, portfolio_ctx)
        return answer

    async def _snapshot_portfolio(self) -> dict[str, Any]:
        """Best-effort portfolio snapshot for decision replay.

        Read-only GET/POST against the Hummingbot API; any failure returns
        {} so a monitoring outage never blocks the trading loop.
        """
        try:
            import httpx

            base = os.getenv("HUMMINGBOT_API_URL", "http://127.0.0.1:8100").rstrip("/")
            auth = (
                os.getenv("HUMMINGBOT_USERNAME", "admin"),
                os.getenv("HUMMINGBOT_PASSWORD", "admin"),
            )
            async with httpx.AsyncClient(base_url=base, auth=auth, timeout=10.0) as client:
                portfolio = (await client.post("/portfolio/state", json={})).json()
                positions = (await client.post("/trading/positions", json={})).json()
            balances = []
            equity = 0.0
            for account, connectors in (portfolio or {}).items():
                for connector, tokens in (connectors or {}).items():
                    for token in tokens or []:
                        value = float(token.get("value") or 0)
                        equity += value
                        balances.append({
                            "account": account, "connector": connector,
                            "token": token.get("token"), "value": value,
                        })
            pos_rows = positions.get("data", []) if isinstance(positions, dict) else []
            return {
                "equity": equity,
                "balances": balances,
                "open_positions": [
                    {
                        "trading_pair": p.get("trading_pair"),
                        "side": p.get("side"),
                        "amount": p.get("amount"),
                        "entry_price": p.get("entry_price"),
                        "unrealized_pnl": p.get("unrealized_pnl"),
                    }
                    for p in pos_rows
                ],
                "open_position_count": len(pos_rows),
            }
        except Exception:
            return {}

    def _record_decision_events(
        self, decision_id: str, answer: str, portfolio_ctx: dict[str, Any]
    ) -> None:
        """Persist structured decision events (analytics path, best-effort)."""
        if self.analytics is None:
            return
        try:
            experiment = self.analytics.get_active_experiment()
            experiment_id = experiment["experiment_id"] if experiment else None
            execution_id = self._created_executors[0] if self._created_executors else None
            risk_status = "BLOCKED" if self._blocked else "PASSED"
            blocks = extract_analysis_blocks(answer)
            common = {
                "mode": self.guard.mode,
                "risk_status": risk_status,
                "market_context": self._market_snapshot,
                "portfolio_context": portfolio_ctx,
                "experiment_id": experiment_id,
                **self.versions,
            }
            if blocks:
                for i, block in enumerate(blocks):
                    self.analytics.record_decision_event(
                        decision_id=decision_id if i == 0 else new_decision_id(),
                        symbol=block.get("symbol"),
                        market_regime=normalize_regime(block.get("regime")),
                        action=block.get("action"),
                        strategy=block.get("strategy"),
                        confidence=block.get("confidence"),
                        evidence=block.get("evidence"),
                        outcome_status="EXECUTED" if execution_id else "RECORDED",
                        execution_id=execution_id if i == 0 else None,
                        **common,
                    )
            else:
                action = (
                    "TRADE" if self._created_executors
                    else "REJECT" if self._blocked
                    else "SCAN"
                )
                self.analytics.record_decision_event(
                    decision_id=decision_id,
                    action=action,
                    outcome_status="EXECUTED" if self._created_executors else "RECORDED",
                    execution_id=execution_id,
                    **common,
                )
        except Exception:
            pass  # analytics must never break the trading path

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
            self._blocked = True
            self._safe_record(
                self.memory.record_decision,
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
        if not result.is_error:
            if name == "manage_executors" and arguments.get("action") == "create":
                executor_id = extract_executor_id(text)
                if executor_id:
                    self._created_executors.append(executor_id)
            elif name == "get_market_data":
                # Keep the freshest market data reply as this run's market
                # snapshot for decision replay (truncated; no reasoning).
                self._market_snapshot = {"tool_result": text[:4000]}
        self._safe_record(
            self.memory.record_decision,
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
        if self._memory_broken:
            raise SafetyViolation(
                "Degraded mode: memory store unavailable — new executors "
                "disabled until memory recovers (analysis still allowed)."
            )
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
