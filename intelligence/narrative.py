"""Narrative Engine (v0.4 Phase 5).

Deterministic, rule-based detection of what the market is currently about.
Combines market movement + social + news + on-chain features into
Narrative objects with explicit confirmation scores per source.

A narrative explains what is happening — it is NEVER a trade signal by
itself (master prompt §11). Narratives flow: evidence -> market
confirmation -> LLM interpretation -> proposal -> cost/risk gates.
"""

from __future__ import annotations

import time
from typing import Any

from intelligence.schema import Narrative
from intelligence.store import IntelligenceStore


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


class NarrativeEngine:
    """Rule set over aggregated features. Each rule produces a Narrative
    when its evidence is strong enough; novelty is computed against recent
    narrative history (same name dominating = low novelty)."""

    def __init__(self, min_strength: float = 0.3):
        self.min_strength = min_strength

    def detect(
        self,
        symbol: str,
        market: dict[str, Any],
        social: dict[str, Any],
        onchain: dict[str, Any],
        news: dict[str, Any],
        store: IntelligenceStore | None = None,
    ) -> list[Narrative]:
        """market: {"24h_change_pct": float, "volume_change_pct": float|None}
        social/news/onchain: output of the feature builders."""
        candidates: list[Narrative] = []
        change = float(market.get("24h_change_pct") or 0)
        news_f = (news.get("symbols") or {}).get(symbol, {})
        news_vol = news_f.get("news_volume_24h", 0)
        news_sev = news_f.get("max_severity_24h", 0)
        news_cats = set(news_f.get("categories") or [])
        fng = ((social.get("market") or {}).get("fear_greed") or {}).get("value")
        net = (onchain.get(symbol) or {}).get("network_activity") or {}
        net_change = float(net.get("change_pct_1d") or 0)
        social_sym = (social.get("symbols") or {}).get(symbol, {})
        social_vol = (social_sym.get("social_volume") or {}).get("value")

        # --- accumulation: quiet-to-positive price + network up + etf/inst. news
        if change > 0 and (net_change > 5 or "etf" in news_cats):
            evidence = [f"price +{change:.1f}%/24h"]
            if net_change > 5:
                evidence.append(f"network activity +{net_change:.1f}%/1d")
            if "etf" in news_cats:
                evidence.append("ETF-related news flow")
            strength = _clip01(0.3 + change / 20 + (0.15 if net_change > 5 else 0)
                               + (0.15 if "etf" in news_cats else 0))
            candidates.append(Narrative(
                name="institutional_accumulation", symbol=symbol,
                strength=strength,
                social_momentum=_clip01(0.5),
                price_confirmation=_clip01(0.5 + change / 10),
                onchain_confirmation=_clip01(0.5 + net_change / 40),
                confidence=0.4 + 0.2 * ("etf" in news_cats),
                evidence=evidence))

        # --- breakout attempt: strong move + news/social momentum
        if abs(change) >= 3:
            evidence = [f"price {change:+.1f}%/24h"]
            if news_vol >= 5:
                evidence.append(f"elevated news volume ({news_vol}/24h)")
            if social_vol:
                evidence.append("social volume data available")
            strength = _clip01(abs(change) / 10 + (0.1 if news_vol >= 5 else 0))
            candidates.append(Narrative(
                name="breakout_attempt" if change > 0 else "breakdown_risk",
                symbol=symbol, strength=strength,
                social_momentum=_clip01(0.4 + (0.2 if news_vol >= 5 else 0)),
                price_confirmation=_clip01(abs(change) / 8),
                confidence=0.5,
                evidence=evidence))

        # --- panic / fear spike: extreme fear + negative move + bad news
        if fng is not None and fng <= 25 and change < 0:
            evidence = [f"fear&greed {fng:.0f} (extreme fear)",
                        f"price {change:+.1f}%/24h"]
            if news_sev >= 0.75:
                evidence.append("high-severity negative news")
            candidates.append(Narrative(
                name="capitulation_risk", symbol=symbol,
                strength=_clip01((25 - fng) / 25 * 0.6 + abs(change) / 20
                                 + (0.15 if news_sev >= 0.75 else 0)),
                social_momentum=_clip01((25 - fng) / 25),
                price_confirmation=_clip01(abs(change) / 10),
                confidence=0.55,
                evidence=evidence))

        # --- regulatory shock: high-severity regulation news, fresh
        if "regulation" in news_cats and news_sev >= 0.75:
            candidates.append(Narrative(
                name="regulatory_shock", symbol=symbol,
                strength=_clip01(news_sev),
                social_momentum=0.4,
                price_confirmation=_clip01(abs(change) / 10),
                confidence=0.6,
                evidence=[f"high-severity regulation news ({news_sev:.2f})"]))

        out = []
        for n in candidates:
            n.novelty = self._novelty(store, n) if store else 0.5
            if n.strength >= self.min_strength:
                out.append(n)
        return sorted(out, key=lambda n: n.strength, reverse=True)

    @staticmethod
    def _novelty(store: IntelligenceStore, n: Narrative) -> float:
        """1.0 = brand new narrative; decays as the same name repeats."""
        recent = store.recent_narratives(symbol=n.symbol, hours=24, limit=20)
        same = [r for r in recent if r["name"] == n.name]
        return _clip01(1.0 - len(same) / 5)
