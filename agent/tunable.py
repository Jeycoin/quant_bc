"""Tunable gate parameters (v1.5): bounded AI control over gate DIALS.

Design red lines (non-negotiable):

- Hard safety boundaries (config `safety:` section, `risk:` ceilings, the
  LIVE_TRADING switch) are NOT tunable. The AI cannot even propose them —
  they are simply absent from the `tunable:` table in settings.yaml, and
  unknown paths are rejected.
- The AI may adjust the parameters listed under `tunable:`, each inside a
  hard-coded [min, max] envelope. Out-of-range requests are CLAMPED, never
  silently accepted; every clamp is an auditable event.
- Every adjustment requires an evidence-based `reason` string. Reasonless
  adjustments are rejected — tuning without a stated hypothesis is how
  gate parameters quietly overfit.
- Scope is PER-DECISION: an adjustment applies to the gates evaluated for
  the decision/executor it arrived with, then evaporates. There is no
  session-persisted or global override path here; global changes still
  require a human edit of settings.yaml. (Session-persistent tuning was
  considered and rejected: a stale adjustment surviving the regime it was
  justified by is strictly worse than re-proposing it — the LLM re-decides
  every 30 minutes anyway.)
- Gates cannot be switched off. There is no `enabled` knob in the tunable
  table; the AI turns dials, it cannot remove the gate.

Consumed by agent/agent.py (live path: gate_adjustments ride inside the
manage_executors/create tool call, because tool calls precede the final
analysis blocks) and scripts/backtest_agent.py (replay path: blocks carry
gate_adjustments directly).
"""

from __future__ import annotations

import copy
import math
from typing import Any

# Executor-config knobs clamped against tunable bounds (these are set by
# the AI per executor, not via gate_adjustments). Barrier fields live
# inside triple_barrier_config after normalization.


def tunable_spec(config: dict[str, Any] | None) -> dict[str, dict[str, float]]:
    """The {path: {default, min, max}} table; {} when not configured."""
    raw = (config or {}).get("tunable", {}) or {}
    out: dict[str, dict[str, float]] = {}
    for path, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        try:
            out[str(path)] = {
                "default": float(spec["default"]),
                "min": float(spec["min"]),
                "max": float(spec["max"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _is_number(v: Any) -> bool:
    # bool is an int subclass — exclude it explicitly, True is not a knob value
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def resolve_adjustments(
    requested: Any,
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate + clamp a gate_adjustments payload.

    Returns {"overrides": {path: applied_value},
             "events":   [{path, requested, applied, clamped, reason}],
             "rejected": [{path, requested, error}]}
    Never raises: a malformed payload yields an empty override set plus
    rejection records — the gates then run on defaults.
    """
    result: dict[str, Any] = {"overrides": {}, "events": [], "rejected": []}
    if requested is None:
        return result
    spec = tunable_spec(config)
    if not isinstance(requested, dict):
        result["rejected"].append({
            "path": None, "requested": str(requested)[:100],
            "error": "malformed: gate_adjustments must be an object"})
        return result
    for path, req in requested.items():
        path = str(path)
        if path not in spec:
            result["rejected"].append({
                "path": path, "requested": str(req)[:100],
                "error": "unknown_path: not in the tunable table "
                         "(hard safety boundaries are never tunable)"})
            continue
        if not isinstance(req, dict) or "value" not in req:
            result["rejected"].append({
                "path": path, "requested": str(req)[:100],
                "error": "malformed: expected {value, reason}"})
            continue
        reason = str(req.get("reason") or "").strip()
        if not reason:
            result["rejected"].append({
                "path": path, "requested": str(req.get("value"))[:100],
                "error": "reason_required: every adjustment needs an "
                         "evidence-based reason"})
            continue
        value = req["value"]
        if not _is_number(value):
            result["rejected"].append({
                "path": path, "requested": str(value)[:100],
                "error": f"bad_type: value must be a finite number, "
                         f"got {type(value).__name__}"})
            continue
        lo, hi = spec[path]["min"], spec[path]["max"]
        clamped = min(max(float(value), lo), hi)
        # integral knobs (e.g. trade_limit) stay integral
        default = spec[path]["default"]
        if float(default).is_integer() and clamped.is_integer():
            clamped = int(clamped)
        result["overrides"][path] = clamped
        result["events"].append({
            "path": path, "requested": float(value), "applied": clamped,
            "clamped": clamped != float(value), "reason": reason[:200]})
    return result


def effective_config(
    config: dict[str, Any] | None,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Deep-copied config with dotted-path overrides applied.

    "entry_gate.rsi_overbought" -> config["entry_gate"]["rsi_overbought"];
    flat keys ("time_stop_hours") land at the top level. The input config
    is never mutated — per-decision scope is enforced by construction.
    """
    eff = copy.deepcopy(config) if config else {}
    for path, value in (overrides or {}).items():
        parts = str(path).split(".")
        node = eff
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value
    return eff


def clamp_executor_knobs(
    cfg: dict[str, Any] | None,
    etype: str,
    config: dict[str, Any] | None,
    price: float | None = None,
) -> list[dict[str, Any]]:
    """Clamp AI-chosen executor knobs (leverage, notional, barriers) against
    the tunable bounds, in place. Returns the clamp events (empty when the
    config was already inside bounds).

    These knobs live on the executor config rather than gate_adjustments
    because they are per-executor properties, not gate thresholds.
    """
    if not isinstance(cfg, dict):
        return []
    spec = tunable_spec(config)
    events: list[dict[str, Any]] = []

    def _clamp(field_path: str, tunable_path: str, container: dict, key: str):
        if tunable_path not in spec or container.get(key) is None:
            return
        try:
            v = float(container[key])
        except (TypeError, ValueError):
            return
        lo, hi = spec[tunable_path]["min"], spec[tunable_path]["max"]
        c = min(max(v, lo), hi)
        if c != v:
            container[key] = c
            events.append({"path": field_path, "requested": v,
                           "applied": c, "clamped": True,
                           "reason": "deterministic clamp to tunable bounds"})

    _clamp("leverage", "leverage", cfg, "leverage")
    barrier = cfg.get("triple_barrier_config")
    if isinstance(barrier, dict):
        if "grid" in etype:
            _clamp("triple_barrier_config.take_profit",
                   "grid_take_profit_pct", barrier, "take_profit")
            _clamp("triple_barrier_config.stop_loss",
                   "grid_stop_loss_pct", barrier, "stop_loss")
        else:
            _clamp("triple_barrier_config.take_profit",
                   "position_take_profit_pct", barrier, "take_profit")
            _clamp("triple_barrier_config.stop_loss",
                   "position_stop_loss_pct", barrier, "stop_loss")
    # notional: quote-sized fields directly; base-unit amount only when a
    # price is supplied (live path resolves it for normalization anyway)
    if "position_notional_quote" in spec:
        lo = spec["position_notional_quote"]["min"]
        hi = spec["position_notional_quote"]["max"]
        for key in ("total_amount_quote", "amount_quote", "notional_quote"):
            if cfg.get(key) is None:
                continue
            try:
                v = float(cfg[key])
            except (TypeError, ValueError):
                continue
            c = min(max(v, lo), hi)
            if c != v:
                cfg[key] = c
                events.append({"path": key, "requested": v, "applied": c,
                               "clamped": True,
                               "reason": "deterministic clamp to tunable bounds"})
        if price and cfg.get("amount") is not None:
            try:
                notional = float(cfg["amount"]) * price
            except (TypeError, ValueError):
                notional = None
            if notional and notional > hi:
                cfg["amount"] = round(hi / price, 8)
                events.append({"path": "notional_quote", "requested": notional,
                               "applied": hi, "clamped": True,
                               "reason": "deterministic clamp to tunable bounds"})
    return events
