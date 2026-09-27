"""Map raw agent decisions to structured timeline events.

The dashboard shows event summaries only — never model reasoning. Event
types follow the agent's OODA-style loop: MARKET_SCAN, ANALYSIS,
STRATEGY_SELECTION, RISK_CHECK, EXECUTOR_CREATED, ORDER, FILL, POSITION,
REVIEW, plus generic housekeeping types.
"""

from __future__ import annotations

import json
from typing import Any

_TOOL_EVENTS = {
    "get_market_data": "MARKET_SCAN",
    "get_portfolio_overview": "ACCOUNT_CHECK",
    "search_history": "RESEARCH",
    "manage_controllers": "STRATEGY_SELECTION",
    "manage_bots": "BOT_MANAGEMENT",
    "setup_connector": "SETUP",
    "set_account_position_mode_and_leverage": "SETUP",
    "explore_dex_pools": "RESEARCH",
    "explore_geckoterminal": "RESEARCH",
    "configure_server": "SETUP",
}

_SYMBOL_KEYS = ("trading_pair", "symbol", "pair")


def _extract_symbol(args: dict[str, Any]) -> str | None:
    for key in _SYMBOL_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    pairs = args.get("trading_pairs")
    if isinstance(pairs, list) and pairs:
        return str(pairs[0])
    cfg = args.get("executor_config")
    if isinstance(cfg, dict):
        return _extract_symbol(cfg)
    return None


def decision_to_event(row: dict[str, Any]) -> dict[str, Any]:
    tool = row.get("tool_name") or "unknown"
    try:
        args = json.loads(row.get("tool_arguments") or "{}")
    except json.JSONDecodeError:
        args = {}
    outcome = row.get("outcome") or ""

    if outcome.startswith("BLOCKED"):
        event_type = "RISK_CHECK"
        result = "BLOCKED"
    elif tool == "manage_executors":
        event_type = {
            "create": "EXECUTOR_CREATED",
            "stop": "EXECUTOR_STOPPED",
        }.get(args.get("action"), "EXECUTOR_MANAGED")
        result = "ERROR" if outcome.startswith("ERROR") else "OK"
    else:
        event_type = _TOOL_EVENTS.get(tool, "TOOL_CALL")
        result = "ERROR" if outcome.startswith("ERROR") else ("OK" if outcome else "—")

    action = args.get("action") or args.get("data_type") or ""
    return {
        "id": row["id"],
        "ts": row["ts"],
        "mode": row.get("mode"),
        "event_type": event_type,
        "symbol": _extract_symbol(args),
        "summary": f"{tool}({action})" if action else tool,
        "result": result,
        "source": "decision",
    }


def analysis_to_event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"a{row['id']}",
        "ts": row["ts"],
        "mode": row.get("mode"),
        "event_type": "ANALYSIS",
        "symbol": row.get("symbol"),
        "summary": f"{row.get('symbol')}: {row.get('action')} · {row.get('regime')}"
                   f" · confidence {row.get('confidence')}",
        "result": row.get("action"),
        "source": "analysis",
    }


def review_to_event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"r{row['id']}",
        "ts": row["ts"],
        "mode": None,
        "event_type": "REVIEW",
        "symbol": None,
        "summary": str(row.get("subject")),
        "result": "RECORDED",
        "source": "review",
    }
