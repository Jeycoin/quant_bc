"""AI Quant Lab analytics: structured decision/trade/review events.

Lives on the analytics path, NOT the real-time trading path: it writes to
its own SQLite file (data/analytics.db) so an analytics failure can never
corrupt agent memory or block trading. Writers must treat every call as
best-effort (see agent/agent.py, which wraps all calls in try/except).
"""

from analytics.store import AnalyticsStore

__all__ = ["AnalyticsStore"]
