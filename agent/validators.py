"""Deterministic validation layer (v0.4).

The LLM proposes; deterministic code approves. Three independent gates run
before any executor is created:

- CostValidator   — positive expected economics after fees, slippage and a
                    safety margin (the bt-replay-7d lesson: grid cycles whose
                    take-profit is below round-trip cost are structurally
                    losing, no matter how good the regime call was)
- RiskValidator   — exposure / concentration / drawdown limits, evaluated
                    from portfolio state, not from the LLM's self-report
- GridProtection  — grid state machine NORMAL/WARNING/DEFENSIVE/EXIT driven
                    by objective market features (and optional external
                    intelligence once Phase 4+ lands)

Nothing here calls an LLM. All thresholds live in config/settings.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GRID_NORMAL = "GRID_NORMAL"
GRID_WARNING = "GRID_WARNING"
GRID_DEFENSIVE = "GRID_DEFENSIVE"
GRID_EXIT = "GRID_EXIT"

# Regime values that make new grid exposure unacceptable.
_GRID_BLOCKING_REGIMES = {"TRENDING_BULL", "TRENDING_BEAR", "REGIME_TRANSITION"}
_GRID_EXIT_REGIMES = {"BREAKOUT"}


# --------------------------------------------------------------------- cost

@dataclass
class CostReport:
    approved: bool
    expected_gross: float
    entry_fee: float
    exit_fee: float
    slippage_cost: float
    safety_margin: float
    net_profit: float
    reason: str

    def summary(self) -> str:
        return (
            f"gross={self.expected_gross:.4f} fees={self.entry_fee + self.exit_fee:.4f}"
            f" slippage={self.slippage_cost:.4f} margin={self.safety_margin:.4f}"
            f" net={self.net_profit:.4f} -> {'APPROVED' if self.approved else 'REJECTED'}"
            f" ({self.reason})"
        )


class CostValidator:
    """minimum_required = entry_fee + exit_fee + slippage + safety_margin.

    Approve only when expected_gross - minimum_required > 0. No hardcoded
    multipliers: every component is explicit (master prompt §18-19).
    """

    def __init__(self, maker_fee: float, taker_fee: float,
                 slippage_pct: float, safety_margin_pct: float):
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.slippage_pct = slippage_pct
        self.safety_margin_pct = safety_margin_pct

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "CostValidator":
        c = (config or {}).get("cost", {})
        return cls(
            maker_fee=float(c.get("maker_fee", 0.0002)),
            taker_fee=float(c.get("taker_fee", 0.0005)),
            slippage_pct=float(c.get("slippage_pct", 0.0002)),
            safety_margin_pct=float(c.get("safety_margin_pct", 0.0003)),
        )

    def validate(self, notional_quote: float, expected_gain_pct: float,
                 maker_entry: bool, maker_exit: bool) -> CostReport:
        gross = notional_quote * expected_gain_pct
        entry_fee = notional_quote * (self.maker_fee if maker_entry else self.taker_fee)
        exit_fee = notional_quote * (self.maker_fee if maker_exit else self.taker_fee)
        slippage = notional_quote * self.slippage_pct
        margin = notional_quote * self.safety_margin_pct
        net = gross - entry_fee - exit_fee - slippage - margin
        reason = ("EXPECTED_NET_PROFIT_POSITIVE" if net > 0
                  else "EXPECTED_NET_PROFIT_NON_POSITIVE")
        return CostReport(net > 0, gross, entry_fee, exit_fee,
                          slippage, margin, net, reason)

    def validate_grid(self, total_amount_quote: float, take_profit_pct: float,
                      max_open_orders: int | None = None,
                      maker_entry: bool = True,
                      maker_exit: bool = True) -> CostReport:
        """Per-cycle economics of a grid: one level buys, one level sells at
        +take_profit_pct. If a single cycle cannot beat its own costs the
        grid is structurally losing regardless of regime."""
        orders = max(int(max_open_orders or 1), 1)
        per_order_quote = total_amount_quote / orders
        return self.validate(per_order_quote, take_profit_pct,
                             maker_entry, maker_exit)

    def validate_position(self, notional_quote: float,
                          take_profit_pct: float) -> CostReport:
        """Directional position: entry and TP may both cross the spread, so
        taker fees on both sides (conservative)."""
        return self.validate(notional_quote, take_profit_pct,
                             maker_entry=False, maker_exit=False)


# --------------------------------------------------------------------- risk

@dataclass
class RiskReport:
    approved: bool
    reasons: list[str] = field(default_factory=list)


class RiskValidator:
    """Portfolio-level limits, evaluated from actual state."""

    def __init__(self, max_total_exposure_quote: float | None = None,
                 max_symbol_exposure_quote: float | None = None,
                 max_drawdown_pct: float | None = None):
        self.max_total_exposure_quote = max_total_exposure_quote
        self.max_symbol_exposure_quote = max_symbol_exposure_quote
        self.max_drawdown_pct = max_drawdown_pct

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "RiskValidator":
        r = (config or {}).get("risk", {})
        return cls(
            max_total_exposure_quote=r.get("max_total_exposure_quote"),
            max_symbol_exposure_quote=r.get("max_symbol_exposure_quote"),
            max_drawdown_pct=r.get("max_drawdown_pct"),
        )

    def validate(self, *, symbol: str | None, new_exposure_quote: float,
                 current_exposure_quote: float,
                 symbol_exposure_quote: float = 0.0,
                 current_drawdown_pct: float | None = None) -> RiskReport:
        reasons: list[str] = []
        if self.max_total_exposure_quote is not None and \
                current_exposure_quote + new_exposure_quote > self.max_total_exposure_quote:
            reasons.append(
                f"total exposure {current_exposure_quote + new_exposure_quote:.2f}"
                f" would exceed max_total_exposure_quote {self.max_total_exposure_quote}")
        if symbol and self.max_symbol_exposure_quote is not None and \
                symbol_exposure_quote + new_exposure_quote > self.max_symbol_exposure_quote:
            reasons.append(
                f"{symbol} exposure {symbol_exposure_quote + new_exposure_quote:.2f}"
                f" would exceed max_symbol_exposure_quote {self.max_symbol_exposure_quote}")
        if self.max_drawdown_pct is not None and current_drawdown_pct is not None and \
                current_drawdown_pct >= self.max_drawdown_pct:
            reasons.append(
                f"current drawdown {current_drawdown_pct:.2f}% >= max_drawdown_pct"
                f" {self.max_drawdown_pct} — new exposure disabled")
        return RiskReport(approved=not reasons, reasons=reasons)


# --------------------------------------------------------- grid protection

@dataclass
class GridState:
    state: str
    reasons: list[str] = field(default_factory=list)


def compute_grid_features(candles: list[dict[str, Any]]) -> dict[str, float | bool]:
    """Objective features for the grid state machine from OHLCV candles.

    Expects ascending candles with high/low/close; needs >= 50 bars for
    EMA50 + a reference window. Returns {} when data is insufficient —
    callers treat that as 'unknown', never as 'safe to grid'.
    """
    if len(candles) < 50:
        return {}
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    price = closes[-1]

    def ema(vals: list[float], n: int) -> float:
        k = 2 / (n + 1)
        e = vals[0]
        for v in vals[1:]:
            e = v * k + e * (1 - k)
        return e

    trend = abs(ema(closes[-40:], 20) - ema(closes[-80:], 50)) / price \
        if len(closes) >= 80 else abs(ema(closes, 20) - ema(closes, 50)) / price
    trend_prev = abs(ema(closes[-41:-1], 20) - ema(closes[-81:-1], 50)) / closes[-2] \
        if len(closes) >= 81 else trend

    trs = [max(highs[i] - lows[i],
               abs(highs[i] - closes[i - 1]),
               abs(lows[i] - closes[i - 1]))
           for i in range(1, len(closes))]
    atr = sum(trs[-14:]) / 14
    atr_pct = atr / price
    atr_pct_prev = (sum(trs[-28:-14]) / 14) / closes[-15] if len(trs) >= 28 else atr_pct

    ref_high = max(highs[-49:-1])
    ref_low = min(lows[-49:-1])
    width = ref_high - ref_low
    near_boundary = width > 0 and min(ref_high - price, price - ref_low) < 0.15 * width
    breakout = price > ref_high + 0.5 * atr or price < ref_low - 0.5 * atr
    return {
        "price": price, "trend_strength": trend, "trend_strength_prev": trend_prev,
        "atr_pct": atr_pct, "atr_pct_prev": atr_pct_prev,
        "range_high": ref_high, "range_low": ref_low,
        "near_boundary": near_boundary, "breakout": breakout,
    }


def evaluate_grid_state(
    features: dict[str, Any] | None = None,
    regime: str | None = None,
    external: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> GridState:
    """Map market features / regime / external intelligence onto the grid
    protection state. `external` carries Phase 4+ signals (narrative_shock,
    social_momentum_abnormal, news_severity) — all optional, all only ever
    escalate, never de-escalate."""
    cfg = (config or {}).get("grid_protection", {})
    warn_trend_rise = float(cfg.get("warn_trend_rise", 1.25))
    defensive_trend = float(cfg.get("defensive_trend_strength", 0.004))
    warn_atr_rise = float(cfg.get("warn_atr_rise", 1.3))

    f = features or {}
    ext = external or {}
    reasons: list[str] = []

    if regime in _GRID_EXIT_REGIMES or f.get("breakout"):
        reasons.append(
            "breakout confirmed" if f.get("breakout") else f"regime {regime}")
        return GridState(GRID_EXIT, reasons)

    defensive = False
    if regime in _GRID_BLOCKING_REGIMES:
        defensive = True
        reasons.append(f"regime {regime}")
    if f.get("trend_strength") is not None and f["trend_strength"] >= defensive_trend:
        defensive = True
        reasons.append(f"trend strength {f['trend_strength']:.4f} >= {defensive_trend}")
    if defensive:
        return GridState(GRID_DEFENSIVE, reasons)

    warning = False
    prev_t = f.get("trend_strength_prev")
    if f.get("trend_strength") is not None and prev_t and \
            f["trend_strength"] > prev_t * warn_trend_rise:
        warning = True
        reasons.append("trend strength rising")
    prev_a = f.get("atr_pct_prev")
    if f.get("atr_pct") is not None and prev_a and f["atr_pct"] > prev_a * warn_atr_rise:
        warning = True
        reasons.append("volatility rising")
    if f.get("near_boundary"):
        warning = True
        reasons.append("price near range boundary")
    if ext.get("narrative_shock"):
        warning = True
        reasons.append("narrative shock detected")
    if ext.get("social_momentum_abnormal"):
        warning = True
        reasons.append("social momentum abnormal")
    if float(ext.get("news_severity") or 0) >= float(cfg.get("news_severity_warn", 0.8)):
        warning = True
        reasons.append("high-severity news event")
    if warning:
        return GridState(GRID_WARNING, reasons)
    return GridState(GRID_NORMAL, ["no escalation signals"])
