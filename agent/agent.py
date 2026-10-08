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
from agent.validators import (
    GRID_DEFENSIVE,
    GRID_EXIT,
    CostValidator,
    RiskValidator,
    compute_grid_features,
    evaluate_grid_state,
    validate_entry,
)
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
    "LOW_VOLATILITY", "BREAKOUT", "UNCERTAIN", "REGIME_TRANSITION",
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
        self._intel_snapshot: dict[str, Any] | None = None
        # Set when a memory write fails — degraded mode: no new trades
        # (master prompt §28: never trade freely when memory is down).
        self._memory_broken = False
        # Deterministic validation gates (v0.4): the LLM proposes, these
        # approve. Fail-closed on missing economics data.
        self.cost_validator = CostValidator.from_config(guard.config)
        self.risk_validator = RiskValidator.from_config(guard.config)

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
        market_feats = await self._market_features_context()
        if market_feats:
            user_message += (
                "\n\n[market_features] fresh multi-horizon features per symbol "
                "(short bars; see the Market features section of your prompt)\n"
                + json.dumps(market_feats, ensure_ascii=False))
        self._intel_snapshot = await self._intel_context()
        if self._intel_snapshot:
            user_message = (
                user_message + "\n\n[intelligence]\n"
                + json.dumps(self._intel_snapshot, ensure_ascii=False))
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
            market_ctx = dict(self._market_snapshot)
            if self._intel_snapshot:
                market_ctx["intelligence"] = self._intel_snapshot
            common = {
                "mode": self.guard.mode,
                "risk_status": risk_status,
                "market_context": market_ctx,
                "portfolio_context": portfolio_ctx,
                "experiment_id": experiment_id,
                "llm_model": self.llm.model,
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

        if name == "manage_executors" and arguments.get("action") == "create":
            rejection, stage = await self._validate_executor_create(arguments)
            if rejection:
                self._blocked = True
                self._safe_record(
                    self.memory.record_decision,
                    mode=self.guard.mode,
                    market_context="",
                    analysis="",
                    tool_name=name,
                    tool_arguments=arguments,
                    outcome=f"BLOCKED[{stage}]: {rejection}",
                )
                self._record_rejection(arguments, stage, rejection)
                return rejection

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

    async def _validate_executor_create(
        self, arguments: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        """Deterministic gates before any executor is created.

        Order: grid protection -> cost -> risk. Returns
        (rejection_message, stage) or (None, None) when all gates pass.
        """
        cfg = arguments.get("executor_config", {}) or {}
        etype = str(arguments.get("executor_type") or cfg.get("type") or "")
        pair = str(cfg.get("trading_pair", ""))
        symbol = pair.partition("-")[0] or None

        # 1. Grid protection: objective candle features; blocks only on
        #    DEFENSIVE/EXIT (WARNING is logged via the rejection-free path).
        if "grid" in etype:
            state = await self._grid_state(cfg)
            if state is not None and state.state in (GRID_DEFENSIVE, GRID_EXIT):
                return (
                    f"GRID PROTECTION — {state.state}: {'; '.join(state.reasons)}. "
                    "New grid exposure is disabled in this state.",
                    "GRID_REJECTED",
                )

        # 1.5 Entry gate (framework v0.5): directional positions must align
        #     with objective higher-horizon factors (8h momentum, EMA cross,
        #     RSI guard). Fail-open when features are unavailable — the
        #     cost/risk gates below still apply.
        if "position" in etype:
            gate = await self._entry_gate_check(cfg)
            if gate is not None and not gate.approved:
                return (
                    "ENTRY GATE — REJECTED: " + "; ".join(gate.reasons),
                    "ENTRY_REJECTED",
                )

        # 2. Cost validator: positive expected economics after fees,
        #    slippage and safety margin. Fails closed when the notional
        #    cannot be determined (no economics data -> no trade).
        report = await self._cost_check(etype, cfg)
        if report is None:
            return (
                "COST VALIDATOR — REJECTED: could not determine trade notional "
                "or take-profit from the executor config / market data.",
                "COST_REJECTED",
            )
        if not report.approved:
            return f"COST VALIDATOR — {report.summary()}", "COST_REJECTED"

        # 3. Risk validator: portfolio-level exposure limits.
        exposure = await self._current_exposure()
        total_exposure = sum(exposure.values()) if isinstance(exposure, dict) else 0.0
        symbol_exposure = (exposure.get(symbol, 0.0)
                           if isinstance(exposure, dict) and symbol else 0.0)
        new_exposure = await self._config_exposure(etype, cfg)
        risk = self.risk_validator.validate(
            symbol=symbol,
            new_exposure_quote=new_exposure or 0.0,
            current_exposure_quote=total_exposure,
            symbol_exposure_quote=symbol_exposure,
        )
        if not risk.approved:
            return "RISK VALIDATOR — REJECTED: " + "; ".join(risk.reasons), \
                "RISK_REJECTED"
        return None, None

    async def _entry_gate_check(self, cfg: dict[str, Any]):
        """Entry gate for position executors: objective 30m market features
        for the target symbol, validated by agent.validators.validate_entry.
        The LLM's regime call is not available here (executor configs do not
        carry it), so only the objective rules apply. Returns None when side
        or features cannot be determined (fail open)."""
        pair = str(cfg.get("trading_pair", ""))
        base = pair.partition("-")[0]
        side_raw = cfg.get("side")
        side_map = {1: "LONG", 2: "SHORT", "1": "LONG", "2": "SHORT",
                    "BUY": "LONG", "SELL": "SHORT", "LONG": "LONG",
                    "SHORT": "SHORT", "buy": "LONG", "sell": "SHORT"}
        action = side_map.get(side_raw)
        if not base or not action:
            return None
        try:
            from agent.market_features import MIN_BARS, compute_market_features

            bar = os.getenv("FEATURE_BAR", "30m")
            data = await self._hb_rest("POST", "/market-data/candles", {
                "connector_name": os.getenv("MARKET_PROBE_CONNECTOR",
                                            "hyperliquid_perpetual"),
                "trading_pair": f"{base}-USD",
                "interval": bar, "max_records": 400,
            })
            rows = data.get("candles", data) if isinstance(data, dict) else data
            candles = []
            for c in rows or []:
                try:
                    ts = float(c["timestamp"])
                    candles.append({
                        "timestamp": ts / 1000 if ts > 1e12 else ts,
                        "open": float(c["open"]), "high": float(c["high"]),
                        "low": float(c["low"]), "close": float(c["close"]),
                        "volume": float(c.get("volume", 0) or 0),
                    })
                except (KeyError, TypeError, ValueError):
                    continue
            candles.sort(key=lambda c: c["timestamp"])
            if len(candles) < MIN_BARS:
                candles = await self._public_candles(base, bar)
            features = compute_market_features(candles)
            if not features:
                return None
            return validate_entry(features, action, regime=None,
                                  config=self.guard.config)
        except Exception:
            return None

    async def _grid_state(self, cfg: dict[str, Any]):
        """Evaluate the grid protection state from objective 1h candles.

        Market data comes from the hyperliquid probe connector (the only
        reachable market-data source in this deployment). Returns None when
        data is unavailable — fail open, the cost/risk gates still apply.
        """
        pair = str(cfg.get("trading_pair", ""))
        base = pair.partition("-")[0]
        if not base:
            return None
        try:
            data = await self._hb_rest("POST", "/market-data/candles", {
                "connector_name": os.getenv("MARKET_PROBE_CONNECTOR",
                                            "hyperliquid_perpetual"),
                "trading_pair": f"{base}-USD",
                "interval": "1h",
                "max_records": 100,
            })
            rows = data.get("candles", data) if isinstance(data, dict) else data
            candles = [
                {"high": float(c["high"]), "low": float(c["low"]),
                 "close": float(c["close"])}
                for c in (rows or [])
            ]
            features = compute_grid_features(candles)
            if not features:
                return None
            return evaluate_grid_state(features, config=self.guard.config)
        except Exception:
            return None

    async def _cost_check(self, etype: str, cfg: dict[str, Any]):
        """Run the cost validator for a grid/position executor config."""
        barrier = cfg.get("triple_barrier_config", {}) or {}
        if "grid" in etype:
            amount = cfg.get("total_amount_quote")
            # grid TP may live in triple_barrier_config
            tp = cfg.get("take_profit", barrier.get("take_profit"))
            if amount is None or tp is None:
                return None
            maker_entry = int(cfg.get("order_type", barrier.get("open_order_type", 3))) == 3
            maker_exit = int(cfg.get("take_profit_order_type",
                                     barrier.get("take_profit_order_type", 3))) == 3
            return self.cost_validator.validate_grid(
                float(amount), float(tp),
                max_open_orders=cfg.get("max_open_orders"),
                maker_entry=maker_entry, maker_exit=maker_exit,
            )
        # position executor: notional = base amount * current price
        amount = cfg.get("amount")
        tp = barrier.get("take_profit")
        if amount is None or tp is None:
            return None
        price = await self._current_price(cfg)
        if not price:
            return None
        # funding is a real holding cost for positions that cross a
        # settlement window (every ~8h); charge it when we would pay,
        # never credit it when we would receive (frontier notes §12)
        side_map = {1: "LONG", 2: "SHORT", "1": "LONG", "2": "SHORT",
                    "BUY": "LONG", "SELL": "SHORT", "LONG": "LONG",
                    "SHORT": "SHORT", "buy": "LONG", "sell": "SHORT"}
        side = side_map.get(cfg.get("side"))
        base = str(cfg.get("trading_pair", "")).split("-")[0] or None
        funding = None
        if base:
            funding = (await self._public_derivatives()).get(base, {}) \
                .get("funding")
        hold_h = float((self.guard.config.get("cost", {}) or {})
                       .get("expected_hold_hours", 8.0))
        return self.cost_validator.validate_position(
            float(amount) * price, float(tp),
            funding_rate=funding, expected_hold_hours=hold_h, side=side,
        )

    async def _current_price(self, cfg: dict[str, Any]) -> float | None:
        connector = str(cfg.get("connector_name", ""))
        pair = str(cfg.get("trading_pair", ""))
        candidates = [(connector, pair)]
        base = pair.partition("-")[0]
        if base:
            candidates.append((os.getenv("MARKET_PROBE_CONNECTOR",
                                         "hyperliquid_perpetual"),
                               f"{base}-USD"))
        for conn, p in candidates:
            if not conn or not p:
                continue
            try:
                data = await self._hb_rest("POST", "/market-data/prices", {
                    "connector_name": conn, "trading_pairs": [p],
                })
                prices = data.get("prices", data) if isinstance(data, dict) else {}
                if prices.get(p):
                    return float(prices[p])
            except Exception:
                continue
        return None

    async def _config_exposure(self, etype: str, cfg: dict[str, Any]) -> float | None:
        if "grid" in etype:
            amount = cfg.get("total_amount_quote")
            return float(amount) if amount is not None else None
        amount = cfg.get("amount")
        price = await self._current_price(cfg)
        if amount is None or not price:
            return None
        return float(amount) * price

    async def _current_exposure(self) -> dict[str, float] | None:
        """Per-symbol quote exposure of RUNNING executors, from their
        configs. None = unknown (monitoring outage -> fail open; the hard
        executor-count cap in _check_position_capacity still applies)."""
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
            rows = result.get("executors", result.get("data", [])) \
                if isinstance(result, dict) else result
            exposure: dict[str, float] = {}
            for ex in rows or []:
                cfg = ex.get("config", {}) or {}
                sym = str(cfg.get("trading_pair", "")).partition("-")[0]
                amount = cfg.get("total_amount_quote")
                if sym and amount is not None:
                    exposure[sym] = exposure.get(sym, 0.0) + float(amount)
            return exposure
        except Exception:
            return None

    def _record_rejection(
        self, arguments: dict[str, Any], stage: str, message: str
    ) -> None:
        """Persist rejected proposals — rejected opportunities are a core
        analytics surface (Dashboard 'Rejected Opportunities')."""
        if self.analytics is None:
            return
        try:
            cfg = arguments.get("executor_config", {}) or {}
            etype = str(arguments.get("executor_type") or cfg.get("type") or "")
            experiment = self.analytics.get_active_experiment()
            self.analytics.record_decision_event(
                mode=self.guard.mode,
                symbol=str(cfg.get("trading_pair", "")).partition("-")[0] or None,
                action="PROPOSE_GRID" if "grid" in etype else "PROPOSE_POSITION",
                strategy="grid" if "grid" in etype else "position",
                risk_status=stage,
                tool_name="manage_executors",
                tool_arguments=arguments,
                outcome_status="REJECTED",
                rejection_reason=message[:500],
                llm_model=self.llm.model,
                experiment_id=experiment["experiment_id"] if experiment else None,
                **self.versions,
            )
        except Exception:
            pass  # analytics must never break the trading path

    async def _intel_context(self) -> dict[str, Any] | None:
        """Build the multi-source intelligence snapshot for the LLM.

        Best-effort on the analytics path: any failure returns None and the
        agent runs with market data only (never blocked by intel outages).
        INTEL_DISABLED=1 turns this off for information-ablation experiments.
        """
        if os.getenv("INTEL_DISABLED", "").strip().lower() in ("1", "true"):
            return None
        try:
            from intelligence.snapshot import build_snapshot
            from intelligence.store import IntelligenceStore

            symbols = self.guard.config["safety"].get("allowed_base_assets",
                                                      ["BTC", "ETH"])
            symbols = [s for s in symbols if not s.startswith("U")]
            # 24h change per symbol for the narrative engine (quick candles
            # call on the market-data probe connector)
            market: dict[str, dict[str, Any]] = {}
            for sym in symbols:
                try:
                    data = await self._hb_rest("POST", "/market-data/candles", {
                        "connector_name": os.getenv("MARKET_PROBE_CONNECTOR",
                                                    "hyperliquid_perpetual"),
                        "trading_pair": f"{sym}-USD",
                        "interval": "1h", "max_records": 30,
                    })
                    rows = data.get("candles", data) if isinstance(data, dict) else data
                    closes = [float(c["close"]) for c in (rows or [])]
                    if len(closes) >= 25:
                        market[sym] = {
                            "24h_change_pct": round((closes[-1] / closes[-25] - 1) * 100, 2)}
                except Exception:
                    continue
            store = IntelligenceStore(
                os.getenv("INTELLIGENCE_DB", "data/intelligence.db"))
            try:
                snapshot = build_snapshot(store, symbols, market=market)
            finally:
                store.close()
            return snapshot.for_llm()
        except Exception:
            return None

    async def _market_features_context(self) -> dict[str, Any] | None:
        """Fresh multi-horizon market features per symbol (short bars).

        Uses the same feature function as the historical replay so live
        decisions and backtests see identical inputs. Best-effort: returns
        None on any failure — the agent then relies on its MCP tools only.
        """
        try:
            from agent.market_features import MIN_BARS, compute_market_features

            symbols = self.guard.config["safety"].get("allowed_base_assets",
                                                      ["BTC", "ETH"])
            symbols = [s for s in symbols if not s.startswith("U")]
            bar = os.getenv("FEATURE_BAR", "30m")
            derivs = await self._public_derivatives()
            out: dict[str, Any] = {}
            for sym in symbols:
                data = await self._hb_rest("POST", "/market-data/candles", {
                    "connector_name": os.getenv("MARKET_PROBE_CONNECTOR",
                                                "hyperliquid_perpetual"),
                    "trading_pair": f"{sym}-USD",
                    "interval": bar, "max_records": 400,
                })
                rows = data.get("candles", data) if isinstance(data, dict) else data
                candles = []
                for c in rows or []:
                    try:
                        ts = float(c["timestamp"])
                        candles.append({
                            "timestamp": ts / 1000 if ts > 1e12 else ts,
                            "open": float(c["open"]), "high": float(c["high"]),
                            "low": float(c["low"]), "close": float(c["close"]),
                            "volume": float(c.get("volume", 0) or 0),
                        })
                    except (KeyError, TypeError, ValueError):
                        continue
                candles.sort(key=lambda c: c["timestamp"])
                if len(candles) < MIN_BARS:
                    # the Hummingbot candles endpoint only serves what the
                    # connector has collected live — fall back to the public
                    # API (read-only market data, same venue) for full depth
                    candles = await self._public_candles(sym, bar)
                feats = compute_market_features(
                    candles,
                    funding_rate=(derivs.get(sym) or {}).get("funding"))
                if feats:
                    out[sym] = feats
            return out or None
        except Exception:
            return None

    async def _public_candles(self, coin: str, interval: str,
                              hours: int = 200) -> list[dict]:
        """Public candle fallback. Default depth ~8 days so the intraday
        deseasonalized volume baseline (market_features, needs >= 4 days)
        works on the live path too."""
        import time as _time

        import httpx

        end_ms = int(_time.time() * 1000)
        start_ms = end_ms - hours * 3600_000
        # trust_env=False: the Windows system proxy must not silently route
        # market-data fetches; set MARKET_DATA_PROXY in .env if needed
        async with httpx.AsyncClient(
            base_url="https://api.hyperliquid.xyz", timeout=30.0,
            trust_env=False, proxy=os.environ.get("MARKET_DATA_PROXY") or None,
        ) as client:
            resp = await client.post("/info", json={
                "type": "candleSnapshot",
                "req": {"coin": coin, "interval": interval,
                        "startTime": start_ms, "endTime": end_ms}})
            resp.raise_for_status()
            batch = resp.json()
        return [{"timestamp": c["t"] / 1000, "open": float(c["o"]),
                 "high": float(c["h"]), "low": float(c["l"]),
                 "close": float(c["c"]), "volume": float(c["v"])}
                for c in batch]

    async def _public_derivatives(self) -> dict[str, dict[str, float]]:
        """Per-coin {funding, open_interest_usd} from the public Hyperliquid
        metaAndAssetCtxs endpoint (same venue as the market-data probe).
        Returns {} on any failure — callers treat missing funding as None."""
        import httpx

        try:
            async with httpx.AsyncClient(
                base_url="https://api.hyperliquid.xyz", timeout=15.0,
                trust_env=False,
                proxy=os.environ.get("MARKET_DATA_PROXY") or None,
            ) as client:
                resp = await client.post("/info", json={"type": "metaAndAssetCtxs"})
                resp.raise_for_status()
                meta, ctxs = resp.json()
            out: dict[str, dict[str, float]] = {}
            for name, ctx in zip(meta.get("universe", []), ctxs):
                coin = name.get("name")
                if not coin:
                    continue
                try:
                    mark = float(ctx.get("markPx", 0) or 0)
                    out[coin] = {
                        "funding": float(ctx.get("funding", 0) or 0),
                        "open_interest_usd": float(ctx.get("openInterest", 0) or 0) * mark,
                    }
                except (TypeError, ValueError):
                    continue
            return out
        except Exception:
            return {}

    async def _hb_rest(self, method: str, path: str, payload: dict[str, Any]) -> Any:
        import httpx

        base = os.getenv("HUMMINGBOT_API_URL", "http://127.0.0.1:8100").rstrip("/")
        auth = (
            os.getenv("HUMMINGBOT_USERNAME", "admin"),
            os.getenv("HUMMINGBOT_PASSWORD", "admin"),
        )
        async with httpx.AsyncClient(base_url=base, auth=auth, timeout=10.0) as client:
            response = await client.request(method, path, json=payload)
            response.raise_for_status()
            return response.json()

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
