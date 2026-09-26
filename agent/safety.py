"""Safety boundary between the LLM and Hummingbot.

The LLM may choose strategies, symbols, directions and tune parameters,
but every tool call passes through SafetyGuard first. The guard enforces
PAPER/LIVE separation and the hard rules from config/settings.yaml.
It never modifies Hummingbot internals — it only allows or denies calls.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import yaml


class SafetyViolation(Exception):
    """Raised when a tool call crosses the configured safety boundary."""


@dataclass
class SafetyGuard:
    live_trading: bool
    config: dict[str, Any]

    @classmethod
    def from_env(cls, config_path: str = "config/settings.yaml") -> "SafetyGuard":
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        live = os.getenv("LIVE_TRADING", "false").strip().lower() == "true"
        return cls(live_trading=live, config=config)

    @property
    def mode(self) -> str:
        return "LIVE" if self.live_trading else "PAPER"

    def check_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """Raise SafetyViolation if the call is not allowed."""
        safety = self.config["safety"]

        blocked = safety.get("blocked_tool_actions", {})
        if tool_name in blocked:
            action = arguments.get("action")
            if action in blocked[tool_name]:
                raise SafetyViolation(
                    f"Action '{action}' on tool '{tool_name}' is blocked by policy."
                )

        if tool_name == "manage_executors" and arguments.get("action") == "create":
            self._check_executor_create(arguments)

    def _check_executor_create(self, arguments: dict[str, Any]) -> None:
        safety = self.config["safety"]
        cfg = arguments.get("executor_config", {}) or {}

        connector = str(cfg.get("connector_name", ""))
        pair = str(cfg.get("trading_pair", ""))

        if not self.live_trading:
            patterns = safety["paper_connector_patterns"]
            if not any(p in connector for p in patterns):
                raise SafetyViolation(
                    f"PAPER mode: connector '{connector}' is not a paper/testnet "
                    f"connector (must match {patterns}). Set LIVE_TRADING=true to trade live."
                )

        if pair:
            base, _, quote = pair.partition("-")
            if quote and quote not in safety["allowed_quote_assets"]:
                raise SafetyViolation(f"Quote asset '{quote}' is not allowed.")
            if base and base not in safety["allowed_base_assets"]:
                raise SafetyViolation(f"Base asset '{base}' is not allowed.")

        amount = cfg.get("total_amount_quote") or cfg.get("amount_quote")
        if amount is not None and float(amount) > safety["max_order_quote_amount"]:
            raise SafetyViolation(
                f"Order amount {amount} exceeds max_order_quote_amount "
                f"{safety['max_order_quote_amount']}."
            )
