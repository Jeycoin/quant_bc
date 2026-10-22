"""Manual-cockpit adapter: a human (or the Kimi session) is the trading brain.

Replaces the LLM call point of the old agent loop. The brain reads a
decision context JSON every N minutes, writes a decision file, and this
script pushes that file through the EXISTING gate stack — nothing about
the six gates is reimplemented here:

    schema -> min_confidence -> safety (SafetyGuard + position capacity)
    -> protections -> grid protection -> entry gate -> cost -> risk

Reused as-is: agent.agent.TradingAgent gate methods (via the __new__
wiring pattern already used in tests), agent.tunable (v1.5 bounded
gate_adjustments), agent.protections, agent.validators, agent.safety,
analytics.store. Executor creation goes through hummingbot_api_client
REST (client.executors.create_executor) with the same payload the old
MCP manage_executors/create call carried — the MCP stdio hop added
nothing but uvx startup cost in a batch CLI.

Usage:
    python scripts/kimi_brain.py context [--out FILE] [--indent]
    python scripts/kimi_brain.py execute decision.json [--dry-run]

Decision file schema (single object, a list, or {"decisions": [...]}):
    {"symbol": "BTC", "action": "WAIT|WATCH|PROPOSE_GRID|PROPOSE_POSITION",
     "regime": "TRENDING_BULL|...", "strategy": "grid|position|none",
     "confidence": 0.0-1.0, "evidence": "one sentence",
     "thesis": "...", "invalidation": "...",       # required for PROPOSE_POSITION
     "ref_price": 81782.0,                          # optional: price at decision
                                                    # time, powers the follow_up
                                                    # tracking in the next context
     "gate_adjustments": {"entry_gate.rsi_overbought": {"value": 80, "reason": "..."}},
     "executor_config": {"connector_name": "binance_perpetual_testnet",
                         "trading_pair": "BTC-USDT", ...}}   # required for PROPOSE_*
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.agent import TradingAgent, normalize_regime  # noqa: E402
from agent.memory.store import MemoryStore  # noqa: E402
from agent.protections import evaluate_protections  # noqa: E402
from agent.safety import SafetyGuard, SafetyViolation, parse_executor_config  # noqa: E402
from agent.tunable import (  # noqa: E402
    clamp_executor_knobs,
    effective_config,
    resolve_adjustments,
    tunable_spec,
)
from agent.validators import (  # noqa: E402
    CostValidator,
    RiskValidator,
    compute_grid_features,
    evaluate_grid_state,
)
from analytics.store import AnalyticsStore, new_decision_id  # noqa: E402
from analytics.versioning import current_versions  # noqa: E402

AGENT_VERSION_TAG = "v1.5-kimi-manual"
LLM_MODEL_TAG = "kimi-code"
ACTIONS = {"WAIT", "WATCH", "PROPOSE_GRID", "PROPOSE_POSITION"}
DEFAULT_EXECUTOR_CONNECTOR = "binance_perpetual_testnet"


# ---------------------------------------------------------------- wiring


def _resolve_repo_path(p: str) -> str:
    path = Path(p)
    return str(path if path.is_absolute() else REPO_ROOT / path)


def build_gate_agent(guard: SafetyGuard, memory: MemoryStore,
                     analytics: AnalyticsStore | None) -> TradingAgent:
    """A TradingAgent carrying only the gate machinery — no LLM, no MCP.

    Same __new__ wiring pattern as tests/test_position_config_normalize.py.
    self.llm is a stub so rejection recording can stamp a model name.
    """
    agent = TradingAgent.__new__(TradingAgent)
    agent.guard = guard
    agent.memory = memory
    agent.llm = SimpleNamespace(model=LLM_MODEL_TAG)
    agent._exit_stack = None
    agent._session = None
    agent._tools = None
    agent._lock = asyncio.Lock()
    agent.versions = current_versions()
    agent.analytics = analytics
    agent._created_executors = []
    agent._blocked = False
    agent._market_snapshot = {}
    agent._intel_snapshot = None
    agent._last_gate_adjustments = None
    agent._memory_broken = False
    agent.cost_validator = CostValidator.from_config(guard.config)
    agent.risk_validator = RiskValidator.from_config(guard.config)
    return agent


def build_stacks() -> tuple[SafetyGuard, MemoryStore, AnalyticsStore | None,
                            TradingAgent]:
    """Load .env + settings, open stores, wire the gate agent."""
    load_dotenv(REPO_ROOT / ".env")
    guard = SafetyGuard.from_env(str(REPO_ROOT / "config" / "settings.yaml"))
    memory = MemoryStore(_resolve_repo_path(
        guard.config["memory"].get("sqlite_path", "data/agent_memory.db")))
    try:
        analytics = AnalyticsStore(_resolve_repo_path(
            os.getenv("ANALYTICS_DB", "data/analytics.db")))
    except Exception as exc:
        print(f"[warn] analytics store unavailable, recording disabled: {exc}",
              file=sys.stderr)
        analytics = None
    return guard, memory, analytics, build_gate_agent(guard, memory, analytics)


# ---------------------------------------------------------------- schema


def load_decisions(payload: Any) -> list[dict[str, Any]]:
    """Accept a single decision object, a list, or {"decisions": [...]}."""
    if isinstance(payload, dict) and isinstance(payload.get("decisions"), list):
        payload = payload["decisions"]
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("decision file must be an object, a list, "
                         "or {\"decisions\": [...]}")
    out = []
    for i, d in enumerate(payload):
        if not isinstance(d, dict):
            raise ValueError(f"decision #{i} is not a JSON object")
        out.append(d)
    if not out:
        raise ValueError("decision file contains no decisions")
    return out


def validate_decision(d: dict[str, Any]) -> list[str]:
    """Schema check; returns a list of errors ([] = valid)."""
    errors: list[str] = []
    action = d.get("action")
    if action not in ACTIONS:
        errors.append(f"action must be one of {sorted(ACTIONS)}, got {action!r}")
    if not d.get("symbol"):
        errors.append("symbol is required")
    conf = d.get("confidence")
    if conf is not None and (not isinstance(conf, (int, float))
                             or isinstance(conf, bool)
                             or not 0.0 <= float(conf) <= 1.0):
        errors.append(f"confidence must be a number in [0, 1], got {conf!r}")
    ga = d.get("gate_adjustments")
    if ga is not None and not isinstance(ga, dict):
        errors.append("gate_adjustments must be an object")
    rp = d.get("ref_price")
    if rp is not None and (not isinstance(rp, (int, float))
                           or isinstance(rp, bool) or float(rp) <= 0):
        errors.append(f"ref_price must be a positive number, got {rp!r}")
    if action in ("PROPOSE_GRID", "PROPOSE_POSITION"):
        cfg = d.get("executor_config")
        if isinstance(cfg, str):
            cfg = parse_executor_config({"executor_config": cfg})
        if not isinstance(cfg, dict) or not cfg:
            errors.append(f"{action} requires a non-empty executor_config object")
        if not d.get("thesis"):
            errors.append(f"{action} requires a thesis (locked at entry)")
        if action == "PROPOSE_POSITION" and not d.get("invalidation"):
            errors.append("PROPOSE_POSITION requires an invalidation condition")
    return errors


# ------------------------------------------------------------ data quality

# Per-section staleness thresholds (minutes). Only sections whose freshest
# data is older than the threshold (or absent) are reported.
DATA_QUALITY_THRESHOLDS = {
    "fear_greed": 360,
    "news": 720,
    "onchain": 720,
    "derivatives": 60,
    "narratives": 120,
}


def assess_data_quality(intel: dict[str, Any] | None) -> dict[str, Any]:
    """Staleness/missing report over the intelligence snapshot.

    Same shape for fresh and stale data is the trap this guards against:
    a 9-minute-old OI and a 33-hour-old network-activity print look
    identical in the JSON unless someone checks age_minutes.
    """
    stale: list[dict[str, Any]] = []
    missing: list[str] = []

    def _check(section: str, age: Any, threshold: int) -> None:
        if age is None:
            missing.append(section)
        elif float(age) > threshold:
            stale.append({"section": section,
                          "age_minutes": round(float(age), 1),
                          "threshold": threshold})

    if not isinstance(intel, dict) or not intel:
        return {"stale": [], "missing": ["intelligence.*"]}

    social = intel.get("social") or {}
    market = social.get("market") or {}
    _check("social.fear_greed",
           (market.get("fear_greed") or {}).get("age_minutes"),
           DATA_QUALITY_THRESHOLDS["fear_greed"])
    sym_social = social.get("symbols") or {}
    if not any(sym_social.get(s) for s in sym_social):
        missing.append("social.symbols.*")

    onchain = intel.get("onchain") or {}
    if not onchain:
        missing.append("onchain.*")
    else:
        for sym, feats in onchain.items():
            if not feats:
                missing.append(f"onchain.{sym}.*")
                continue
            for name, item in feats.items():
                _check(f"onchain.{sym}.{name}",
                       (item or {}).get("age_minutes"),
                       DATA_QUALITY_THRESHOLDS["onchain"])

    derivs = intel.get("derivatives") or {}
    if not derivs:
        missing.append("derivatives.*")
    for sym, feats in derivs.items():
        for name in ("open_interest", "funding_rate"):
            _check(f"derivatives.{sym}.{name}",
                   ((feats or {}).get(name) or {}).get("age_minutes"),
                   DATA_QUALITY_THRESHOLDS["derivatives"])

    news = intel.get("news") or []
    if not news:
        missing.append("news")
    else:
        ages = [n.get("age_minutes") for n in news
                if n.get("age_minutes") is not None]
        _check("news(newest)", min(ages) if ages else None,
               DATA_QUALITY_THRESHOLDS["news"])

    narratives = intel.get("narratives") or []
    if not narratives:
        missing.append("narratives")
    else:
        ages = [n.get("age_minutes") for n in narratives
                if n.get("age_minutes") is not None]
        _check("narratives(newest)", min(ages) if ages else None,
               DATA_QUALITY_THRESHOLDS["narratives"])

    return {"stale": stale, "missing": missing}


# ------------------------------------------------------- executor pipeline


def executor_type_for(decision: dict[str, Any], cfg: dict[str, Any]) -> str:
    etype = str(cfg.get("type") or "")
    if not etype:
        etype = ("grid_executor" if decision["action"] == "PROPOSE_GRID"
                 else "position_executor")
    return etype


def prepare_arguments(decision: dict[str, Any],
                      config: dict[str, Any]) -> dict[str, Any]:
    """Build the manage_executors/create-equivalent arguments dict.

    Defaults: connector = binance_perpetual_testnet (env overridable),
    pair = <symbol>-USDT. Everything explicit in executor_config wins.
    """
    cfg = dict(parse_executor_config(
        {"executor_config": decision.get("executor_config") or {}}))
    cfg.setdefault("connector_name",
                   os.getenv("EXECUTOR_CONNECTOR", DEFAULT_EXECUTOR_CONNECTOR))
    if not cfg.get("trading_pair") and decision.get("symbol"):
        cfg["trading_pair"] = f"{decision['symbol']}-USDT"
    etype = executor_type_for(decision, cfg)
    cfg["type"] = etype
    return {"action": "create", "executor_type": etype, "executor_config": cfg}


async def static_pipeline(agent: TradingAgent, decision: dict[str, Any],
                          arguments: dict[str, Any]
                          ) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The pre-gate transformations, in the same order as agent.py
    _call_tool: keep_position force / position normalization, then
    gate_adjustments resolve + executor-knob clamps (v1.5). Returns
    (gate_report, gate_config)."""
    cfg = arguments["executor_config"]
    etype = arguments["executor_type"]
    if "grid" in etype:
        cfg["keep_position"] = False  # v1.4.1: grids never keep inventory
    if "position" in etype:
        await agent._normalize_position_config(cfg)

    gate_report = resolve_adjustments(decision.get("gate_adjustments"),
                                      agent.guard.config)
    gate_config = (effective_config(agent.guard.config,
                                    gate_report["overrides"])
                   if gate_report["overrides"] else None)
    gate_report["events"] += clamp_executor_knobs(
        cfg, etype, agent.guard.config)

    # time_stop_hours is a real barrier here (in the old LLM loop the knob
    # was recorded but inert — live positions took time_limit from the
    # executor config only). Replay parity: default 6h time-stop.
    if "position" in etype:
        barrier = cfg.get("triple_barrier_config")
        if isinstance(barrier, dict) and barrier.get("time_limit") is None:
            eff = gate_config or agent.guard.config
            hours = float(eff.get("time_stop_hours")
                          or tunable_spec(agent.guard.config)
                          .get("time_stop_hours", {}).get("default", 6))
            barrier["time_limit"] = int(hours * 3600)
    return gate_report, gate_config


def check_min_confidence(decision: dict[str, Any],
                         gate_config: dict[str, Any] | None,
                         config: dict[str, Any]) -> str | None:
    """Replay-parity confidence floor (the old LLM loop had none — tool
    calls carry no confidence; kimi decisions do). None = pass."""
    conf = decision.get("confidence")
    if conf is None:
        return None
    eff = gate_config or config
    floor = eff.get("min_confidence")
    if floor is None:
        floor = tunable_spec(config).get("min_confidence", {}) \
            .get("default", 0.6)
    if float(conf) < float(floor):
        return (f"CONFIDENCE GATE — REJECTED: confidence {conf} < "
                f"min_confidence {floor}")
    return None


# --------------------------------------------------------------- recording


class KimiBrain:
    """Holds the stores + gate agent; network seams are methods so tests
    can stub them."""

    def __init__(self, guard: SafetyGuard, memory: MemoryStore,
                 analytics: AnalyticsStore | None, agent: TradingAgent):
        self.guard = guard
        self.memory = memory
        self.analytics = analytics
        self.agent = agent
        self.versions = current_versions()

    # -- network seams (stubbed in tests) --

    async def create_executor(self, cfg: dict[str, Any]) -> dict[str, Any]:
        """REST equivalent of the old MCP manage_executors/create call."""
        from hummingbot_api_client import HummingbotAPIClient

        client = HummingbotAPIClient(
            base_url=os.getenv("HUMMINGBOT_API_URL", "http://127.0.0.1:8100"),
            username=os.getenv("HUMMINGBOT_USERNAME", "admin"),
            password=os.getenv("HUMMINGBOT_PASSWORD", "admin"),
        )
        await client.init()
        try:
            return await client.executors.create_executor(cfg)
        finally:
            await client.close()

    async def running_executors(self) -> list[dict[str, Any]]:
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
        out = []
        for ex in rows or []:
            cfg = ex.get("config", {}) or {}
            out.append({
                "id": ex.get("id") or ex.get("executor_id"),
                "type": cfg.get("type"),
                "pair": cfg.get("trading_pair"),
                "connector": cfg.get("connector_name"),
                "side": cfg.get("side"),
                "amount_quote": cfg.get("total_amount_quote"),
                "status": ex.get("status"),
                "pnl_quote": ex.get("net_pnl_quote"),
            })
        return out

    async def portfolio_snapshot(self) -> dict[str, Any]:
        return await self.agent._snapshot_portfolio()

    # -- recording --

    @staticmethod
    def _evidence_text(decision: dict[str, Any]) -> Any:
        evidence = decision.get("evidence")
        if isinstance(evidence, (list, tuple)):
            return "; ".join(str(e) for e in evidence)
        return evidence

    def _record(self, decision: dict[str, Any], decision_id: str, *,
                outcome_status: str, risk_status: str | None = None,
                rejection_reason: str | None = None,
                execution_id: str | None = None,
                gate_report: dict[str, Any] | None = None,
                tool_arguments: dict[str, Any] | None = None,
                portfolio_ctx: dict[str, Any] | None = None,
                dry_run: bool = False) -> None:
        if self.analytics is None:
            return
        experiment = self.analytics.get_active_experiment()
        market_ctx: dict[str, Any] = {}
        if decision.get("thesis"):
            market_ctx["thesis"] = decision["thesis"]
        if decision.get("invalidation"):
            market_ctx["invalidation"] = decision["invalidation"]
        if decision.get("ref_price") is not None:
            market_ctx["ref_price"] = decision["ref_price"]
        if gate_report and (gate_report["events"] or gate_report["rejected"]):
            market_ctx["applied_gate_adjustments"] = gate_report
        if dry_run:
            market_ctx["dry_run"] = True
        self.analytics.record_decision_event(
            decision_id=decision_id,
            mode=self.guard.mode,
            symbol=decision.get("symbol"),
            market_regime=normalize_regime(decision.get("regime")),
            action=decision.get("action"),
            strategy=decision.get("strategy"),
            confidence=decision.get("confidence"),
            evidence=self._evidence_text(decision),
            risk_status=risk_status,
            tool_name="manage_executors" if tool_arguments else None,
            tool_arguments=tool_arguments,
            outcome_status=outcome_status,
            execution_id=execution_id,
            market_context=market_ctx,
            portfolio_context=portfolio_ctx or {},
            experiment_id=experiment["experiment_id"] if experiment else None,
            rejection_reason=rejection_reason,
            llm_model=LLM_MODEL_TAG,
            agent_version=AGENT_VERSION_TAG,
            prompt_version=self.versions["prompt_version"],
            config_version=self.versions["config_version"],
        )

    def _record_analysis_memory(self, decision: dict[str, Any]) -> None:
        """Keep the memory trail continuous with the old loop. A failure
        flips degraded mode on the gate agent — its capacity check then
        blocks new executors (master prompt section 28)."""
        try:
            self.memory.record_analysis(
                mode=self.guard.mode,
                symbol=decision.get("symbol"),
                regime=decision.get("regime"),
                trend=decision.get("trend"),
                volatility=decision.get("volatility"),
                action=decision.get("action"),
                strategy=decision.get("strategy"),
                confidence=decision.get("confidence"),
                evidence=self._evidence_text(decision),
                raw=json.dumps(decision, ensure_ascii=False),
            )
        except Exception as exc:
            self.agent._memory_broken = True
            print(f"[warn] memory write failed, degraded mode on: {exc}",
                  file=sys.stderr)

    # -- execute --

    async def execute_decision(self, decision: dict[str, Any],
                               dry_run: bool = False) -> dict[str, Any]:
        decision_id = new_decision_id()
        symbol = decision.get("symbol")
        action = decision.get("action")

        errors = validate_decision(decision)
        if errors:
            reason = "SCHEMA — REJECTED: " + "; ".join(errors)
            self._record(decision, decision_id, outcome_status="REJECTED",
                         risk_status="SCHEMA_INVALID",
                         rejection_reason=reason[:500])
            return {"decision_id": decision_id, "symbol": symbol,
                    "action": action, "outcome": "REJECTED",
                    "stage": "SCHEMA_INVALID", "reason": reason}

        self._record_analysis_memory(decision)

        if action in ("WAIT", "WATCH"):
            self._record(decision, decision_id, outcome_status="RECORDED")
            return {"decision_id": decision_id, "symbol": symbol,
                    "action": action, "outcome": "RECORDED"}

        arguments = prepare_arguments(decision, self.guard.config)
        gate_report, gate_config = await static_pipeline(
            self.agent, decision, arguments)

        rejection = check_min_confidence(decision, gate_config,
                                         self.guard.config)
        stage = "CONF_REJECTED" if rejection else None

        if not rejection:
            try:
                self.guard.check_tool_call("manage_executors", arguments)
                await self.agent._check_position_capacity(
                    "manage_executors", arguments)
            except SafetyViolation as exc:
                rejection, stage = f"SAFETY VIOLATION — {exc}", "SAFETY"

        if not rejection:
            rejection, stage = await self.agent._validate_executor_create(
                arguments, gate_config=gate_config)

        if rejection:
            self._record(decision, decision_id, outcome_status="REJECTED",
                         risk_status=stage,
                         rejection_reason=str(rejection)[:500],
                         gate_report=gate_report, tool_arguments=arguments)
            return {"decision_id": decision_id, "symbol": symbol,
                    "action": action, "outcome": "REJECTED", "stage": stage,
                    "reason": str(rejection),
                    "gate_adjustments": gate_report}

        if dry_run:
            self._record(decision, decision_id, outcome_status="RECORDED",
                         risk_status="PASSED", gate_report=gate_report,
                         tool_arguments=arguments, dry_run=True)
            return {"decision_id": decision_id, "symbol": symbol,
                    "action": action, "outcome": "DRY_RUN_PASSED",
                    "executor_config": arguments["executor_config"],
                    "gate_adjustments": gate_report}

        cfg = arguments["executor_config"]
        try:
            response = await self.create_executor(cfg)
        except Exception as exc:
            reason = f"EXECUTOR CREATE FAILED: {exc}"
            self._record(decision, decision_id, outcome_status="REJECTED",
                         risk_status="EXECUTION_ERROR",
                         rejection_reason=reason[:500],
                         gate_report=gate_report, tool_arguments=arguments)
            return {"decision_id": decision_id, "symbol": symbol,
                    "action": action, "outcome": "ERROR",
                    "stage": "EXECUTION_ERROR", "reason": reason}

        executor_id = (response.get("executor_id") or response.get("id")
                       if isinstance(response, dict) else None)
        portfolio_ctx = {}
        try:
            portfolio_ctx = await self.portfolio_snapshot()
        except Exception:
            pass
        self._record(decision, decision_id, outcome_status="EXECUTED",
                     risk_status="PASSED", execution_id=executor_id,
                     gate_report=gate_report, tool_arguments=arguments,
                     portfolio_ctx=portfolio_ctx)
        return {"decision_id": decision_id, "symbol": symbol,
                "action": action, "outcome": "EXECUTED",
                "executor_id": executor_id,
                "create_response": response,
                "gate_adjustments": gate_report}

    async def execute_all(self, decisions: list[dict[str, Any]],
                          dry_run: bool = False) -> list[dict[str, Any]]:
        """Batch entry point: one command processes every symbol's decision
        (single-object files are normalized to a 1-element list upstream,
        so the old single-decision format keeps working)."""
        results = []
        for decision in decisions:
            results.append(await self.execute_decision(
                decision, dry_run=dry_run))
        return results

    # -- context helpers: live/replay trade split --

    def _classified_trades(self, symbol: str,
                           limit: int = 40) -> list[dict[str, Any]]:
        """Closed trades for a symbol with a live/replay tag.

        Replay rows are identified by executor_id 'bt-*' or close_type
        REPLAY_END (replay also uses regular close types like STOP_LOSS,
        so close_type alone is not enough). The brain's track record must
        be built on LIVE fills only; replay rows stay visible but tagged.
        """
        if self.analytics is None:
            return []
        try:
            rows = self.analytics._conn.execute(
                "SELECT side, close_type, pnl_quote, ts_close, executor_id"
                " FROM trade_events WHERE symbol = ? AND status = 'CLOSED'"
                " AND ts_close IS NOT NULL ORDER BY ts_close DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        except Exception:
            return []
        now = time.time()
        out = []
        for side, close_type, pnl, ts_close, exid in rows:
            src = ("replay" if (str(exid or "").startswith("bt-")
                                or close_type == "REPLAY_END") else "live")
            out.append({
                "side": side, "close": close_type,
                "pnl": round(pnl, 2) if pnl is not None else None,
                "age_h": round((now - ts_close) / 3600, 1), "src": src,
            })
        return out

    @staticmethod
    def _split_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
        """track_record split into live/replay sub-records. An empty live
        sample is stated explicitly ({\"trades\": 0}) — never hidden."""
        def stats(sub: list[dict[str, Any]]) -> dict[str, Any]:
            pnls = [t["pnl"] for t in sub if t["pnl"] is not None]
            if not pnls:
                return {"trades": 0}
            out: dict[str, Any] = {"trades": len(pnls),
                                   "total_pnl": round(sum(pnls), 2)}
            if len(pnls) < 3:
                out["note"] = "sample too small for win rate"
                return out
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            out.update({
                "win_rate": round(len(wins) / len(pnls), 2),
                "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
                "avg_loss": (round(sum(losses) / len(losses), 2)
                             if losses else None),
            })
            return out

        live = [t for t in trades if t["src"] == "live"][:20]
        replay = [t for t in trades if t["src"] == "replay"][:20]
        return {"live": stats(live), "replay": stats(replay)}

    def _apply_trade_split(self, entry: dict[str, Any], symbol: str) -> None:
        trades = self._classified_trades(symbol)
        entry["recent_trades"] = trades[:4]
        entry["track_record"] = self._split_stats(trades)

    # -- context helpers: decision follow-up --

    @staticmethod
    def _verdict(action: str, side: Any, change_pct: float | None,
                 executed: bool) -> str:
        """Rule-based one-liner on how the last decision aged."""
        suffix = ""
        if action.startswith("PROPOSE") and not executed:
            suffix = "(未成交)"
        if change_pct is None:
            return f"无 ref_price 或现价,无法跟踪{suffix}"
        c = change_pct
        if action in ("WAIT", "WATCH"):
            # direction-aware: after WAIT/WATCH a price DROP means the
            # caution was correct (loss avoided); a RISE is opportunity
            # cost worth re-examining
            if c <= -0.3:
                comment = f"观望正确,规避下跌 {c:+.2f}%"
            elif c >= 0.3:
                comment = f"错过反弹 {c:+.2f}%,复核是否该更积极"
            else:
                comment = "横盘,判断暂未被证伪"
            return f"{action} 后价格 {c:+.2f}%({comment})"
        if action == "PROPOSE_GRID":
            comment = ("价格偏离区间假设,需复盘" if abs(c) >= 0.5
                       else "区间假设暂成立")
            return f"GRID 后价格 {c:+.2f}%({comment}){suffix}"
        direction = {"LONG": 1, "BUY": 1, "1": 1,
                     "SHORT": -1, "SELL": -1, "2": -1}.get(
                         str(side or "").upper(), 0)
        if direction == 0:
            return f"价格 {c:+.2f}%(方向未知,无法判定){suffix}"
        aligned = c * direction
        if aligned > 0.5:
            comment = "判断被验证"
        elif aligned < -0.5:
            comment = "判断被证伪,需复盘"
        else:
            comment = "横盘,暂未被证伪"
        return f"{side} 后价格 {c:+.2f}%({comment}){suffix}"

    def _follow_up(self, symbols: list[str],
                   market_features: dict[str, Any],
                   portfolio: dict[str, Any]) -> dict[str, Any]:
        """How each symbol's latest kimi-code decision is holding up:
        thesis/invalidation carried forward, price then vs now, and a
        rule-based verdict. Open positions report unrealized PnL."""
        if self.analytics is None:
            return {}
        positions: dict[str, Any] = {}
        for p in (portfolio or {}).get("open_positions") or []:
            sym = str(p.get("trading_pair") or "").partition("-")[0]
            if sym:
                positions[sym] = p
        out: dict[str, Any] = {}
        for sym in symbols:
            try:
                row = self.analytics._conn.execute(
                    "SELECT ts, action, confidence, outcome_status,"
                    " market_context, tool_arguments FROM decision_events"
                    " WHERE llm_model = ? AND symbol = ?"
                    # skip subagent smoke-test rows (fake ref_prices made
                    # absurd verdicts) and dry-run records
                    " AND (evidence IS NULL"
                    "      OR lower(evidence) NOT LIKE '%smoke test%')"
                    " AND (market_context IS NULL"
                    "      OR market_context NOT LIKE '%\"dry_run\": true%')"
                    " ORDER BY ts DESC LIMIT 1",
                    (LLM_MODEL_TAG, sym),
                ).fetchone()
            except Exception:
                row = None
            if not row:
                continue
            ts, action, conf, outcome, mc_raw, ta_raw = row
            try:
                mc = json.loads(mc_raw or "{}")
            except (json.JSONDecodeError, TypeError):
                mc = {}
            side = None
            try:
                ta = json.loads(ta_raw or "{}")
                side = (ta.get("executor_config") or {}).get("side")
            except (json.JSONDecodeError, TypeError):
                pass
            ref_price = mc.get("ref_price")
            price_now = (market_features.get(sym) or {}).get("price")
            change_pct = (round((float(price_now) / float(ref_price) - 1) * 100, 2)
                          if ref_price and price_now else None)
            entry: dict[str, Any] = {
                "age_min": round((time.time() - ts) / 60, 1),
                "action": action, "conf": conf, "outcome": outcome,
                "ref_price": ref_price, "price_now": price_now,
                "change_pct": change_pct,
                "verdict": self._verdict(action, side, change_pct,
                                         outcome == "EXECUTED"),
            }
            if mc.get("thesis"):
                entry["thesis"] = str(mc["thesis"])[:120]
            if mc.get("invalidation"):
                entry["invalidation"] = str(mc["invalidation"])[:120]
            pos = positions.get(sym)
            if pos is not None:
                entry["open_position_pnl"] = pos.get("unrealized_pnl")
            out[sym] = entry
        return out

    # -- context --

    async def build_context(self) -> dict[str, Any]:
        agent = self.agent
        symbols = [s for s in self.guard.config["safety"]
                   .get("allowed_base_assets", ["BTC", "ETH"])
                   if not s.startswith("U")]

        mf_errors: dict[str, str] = {}
        market_features = await agent._market_features_context(
            errors=mf_errors) or {}
        missing = [s for s in symbols if s not in market_features]
        if missing:
            # one retry for the missing set: transient single-venue hiccups
            # usually clear immediately; successes from pass 1 are kept
            retry = await agent._market_features_context(
                errors=mf_errors) or {}
            for sym, feats in retry.items():
                market_features.setdefault(sym, feats)
            missing = [s for s in symbols if s not in market_features]
        market_features = market_features or None
        portfolio = {}
        try:
            portfolio = await agent._snapshot_portfolio()
        except Exception:
            pass
        try:
            executors = await self.running_executors()
        except Exception as exc:
            executors = {"error": str(exc)[:200]}
        intel = await agent._intel_context()
        memory_ctx = await agent._memory_context() or {}
        # live/replay split: the shared _memory_context mixes replay fills
        # into recent_trades/track_record — re-tag and split them so the
        # brain sees real live performance (pain point: memory pollution)
        for sym in symbols:
            self._apply_trade_split(memory_ctx.setdefault(sym, {}), sym)

        prot = None
        if self.analytics is not None:
            try:
                report = evaluate_protections(
                    self.analytics.closed_trades(), time.time(),
                    config=self.guard.config)
                prot = {
                    "global_lock": report.global_lock,
                    "reasons": report.reasons,
                    "symbol_locks": report.symbol_locks,
                    "until_ts": report.until_ts,
                }
            except Exception as exc:
                prot = {"error": str(exc)[:200]}

        grid_states: dict[str, Any] = {}
        for sym in symbols:
            grid_states[sym] = await self._context_grid_state(sym)

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "mode": self.guard.mode,
            "brain": {"agent_version": AGENT_VERSION_TAG,
                      "llm_model": LLM_MODEL_TAG, **self.versions},
            "data_sources": {
                "market_probe_connector": os.getenv(
                    "MARKET_PROBE_CONNECTOR", "hyperliquid_perpetual"),
                "feature_bar": os.getenv("FEATURE_BAR", "30m"),
                "executor_connector": os.getenv(
                    "EXECUTOR_CONNECTOR", DEFAULT_EXECUTOR_CONNECTOR),
            },
            "symbols": symbols,
            "market_features": market_features,
            # outage visibility for the brain: [] / {} = all symbols fine;
            # a partial list = single-symbol fault (down-weight that symbol),
            # all five = venue/collection outage (treat context as degraded)
            "market_features_missing": missing,
            "market_features_errors": {s: mf_errors[s] for s in missing
                                       if s in mf_errors},
            "portfolio": portfolio,
            "active_executors": executors,
            "intelligence": intel,
            "data_quality": assess_data_quality(intel),
            "follow_up": self._follow_up(symbols, market_features or {},
                                         portfolio),
            "memory": memory_ctx,
            "gates": {
                "tunable": tunable_spec(self.guard.config),
                "protections": prot,
                "grid_state": grid_states,
                "hard_limits": {
                    "max_order_quote_amount": self.guard.config["safety"]
                        .get("max_order_quote_amount"),
                    "max_open_positions": self.guard.config["safety"]
                        .get("max_open_positions"),
                    "risk": self.guard.config.get("risk", {}),
                    "entry_gate": self.guard.config.get("entry_gate", {}),
                },
            },
            "recent_decisions": self._recent_decisions(),
        }

    async def _context_grid_state(self, symbol: str) -> dict[str, Any] | None:
        """Grid-protection state for the context output. Unlike the gate's
        own _grid_state (HB candles endpoint only, fail-open when the
        connector has no collected history), this uses the public candle
        fallback so the brain actually SEES the state. Context-only — the
        gate path itself is unchanged."""
        try:
            candles = await self.agent._public_candles(symbol, "1h", hours=150)
            features = compute_grid_features(candles)
            if not features:
                return None
            state = evaluate_grid_state(features, config=self.guard.config)
            return {"state": state.state, "reasons": state.reasons,
                    "trend_strength": round(features["trend_strength"], 5),
                    "atr_pct": round(features["atr_pct"], 5),
                    "near_boundary": features["near_boundary"],
                    "breakout": features["breakout"]}
        except Exception:
            return None

    def _recent_decisions(self, limit: int = 10) -> list[dict[str, Any]]:
        if self.analytics is None:
            return []
        try:
            rows = self.analytics._conn.execute(
                "SELECT decision_id, ts, symbol, market_regime, action,"
                " strategy, confidence, outcome_status, risk_status,"
                " rejection_reason, llm_model FROM decision_events"
                " ORDER BY ts DESC LIMIT ?", (limit,),
            ).fetchall()
        except Exception:
            return []
        now = time.time()
        return [{
            "decision_id": r[0], "age_min": round((now - r[1]) / 60, 1),
            "symbol": r[2], "regime": r[3], "action": r[4], "strategy": r[5],
            "conf": r[6], "outcome": r[7], "risk_status": r[8],
            "rejection": (r[9] or "")[:120] or None, "model": r[10],
        } for r in rows]


# -------------------------------------------------------------------- CLI


async def _cmd_context(args: argparse.Namespace) -> int:
    guard, memory, analytics, agent = build_stacks()
    brain = KimiBrain(guard, memory, analytics, agent)
    try:
        ctx = await brain.build_context()
    finally:
        if analytics is not None:
            analytics.close()
        memory.close()
    text = json.dumps(ctx, ensure_ascii=False,
                      indent=args.indent if args.indent else None,
                      separators=None if args.indent else (",", ":"))
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"context written to {args.out} ({len(text)} chars)")
    else:
        print(text)
    return 0


async def _cmd_execute(args: argparse.Namespace) -> int:
    try:
        payload = json.loads(Path(args.decision_file).read_text(
            encoding="utf-8"))
        decisions = load_decisions(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: cannot load decisions: {exc}", file=sys.stderr)
        return 2

    guard, memory, analytics, agent = build_stacks()
    brain = KimiBrain(guard, memory, analytics, agent)
    try:
        results = await brain.execute_all(decisions, dry_run=args.dry_run)
    finally:
        if analytics is not None:
            analytics.close()
        memory.close()
    print(json.dumps({"dry_run": args.dry_run, "mode": guard.mode,
                      "results": results}, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Manual-cockpit adapter: the human/Kimi session is "
                    "the trading brain; this script is its hands.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_ctx = sub.add_parser("context", help="dump the decision context JSON")
    p_ctx.add_argument("--out", help="write to file instead of stdout")
    p_ctx.add_argument("--indent", type=int, default=0,
                       help="pretty-print with N spaces")
    p_ex = sub.add_parser("execute", help="validate + execute a decision file")
    p_ex.add_argument("decision_file")
    p_ex.add_argument("--dry-run", action="store_true",
                      help="validate + record only; no executor is created")
    args = parser.parse_args()

    if args.command == "context":
        rc = asyncio.run(_cmd_context(args))
    else:
        rc = asyncio.run(_cmd_execute(args))
    sys.exit(rc)


if __name__ == "__main__":
    main()
