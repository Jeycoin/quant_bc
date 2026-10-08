"""Cross-sectional ranking layer (framework v0.6, L1).

Design: docs/research/multi-asset-framework-design.md. Evidence base
(docs/research/crypto-quant-frontier-notes.md §7-10, §11-14):

- Cross-sectional momentum in crypto is WEAK and concentrated in the long
  leg of large caps (Han/Kang/Ryu) — so this layer only ranks for the
  long side; shorts face a much higher bar (bottom rank + higher-timeframe
  downtrend + halved budget, applied by callers).
- Funding's alpha is cross-sectional (Presto): the coin with funding far
  above its peers is crowded — penalize it, never time with it.
- Volume confirmation must be deseasonalized (§11) when available.
- Ranking is DETERMINISTIC. The LLM never ranks; it only times entries
  inside the candidate set this layer produces.

Pure functions, no I/O, no LLM — unit-testable by construction.
"""

from __future__ import annotations

from typing import Any

DEFAULT_WEIGHTS = {
    "mom_8h": 1.0,        # ret_16bar_pct at 30m bars
    "mom_24h": 1.0,       # ret_48bar_pct
    "trend_quality": 1.0,  # ema_dist_pct (signed EMA8/21 distance)
    "volume_confirm": 1.0,  # deseasonalized volume z signed by momentum
    "funding_crowding": 1.0,  # penalty only (crowded longs get marked down)
}


def _zscores(values: dict[str, float]) -> dict[str, float]:
    """Cross-sectional z-score. All-identical input -> all zeros (the
    factor carries no information when the market moves as one)."""
    n = len(values)
    if n < 2:
        return {k: 0.0 for k in values}
    mu = sum(values.values()) / n
    var = sum((v - mu) ** 2 for v in values.values()) / n
    sd = var ** 0.5
    if sd <= 1e-12:
        return {k: 0.0 for k in values}
    return {k: (v - mu) / sd for k, v in values.items()}


def score_symbols(
    features_by_symbol: dict[str, dict[str, Any]],
    weights: dict[str, float] | None = None,
    top_n: int = 2,
    min_dispersion: float = 0.5,
    funding: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    """Rank symbols by relative strength. Returns:

    ranking          symbols sorted by composite score (best first)
    scores           composite score per symbol
    factor_z         per-factor cross-sectional z-scores (diagnostics)
    dispersion       std of composite scores — low means the whole market
                     moves as one and cross-sectional choice is noise
    rotation         BTC_FAVOR / ALT_FAVOR / NEUTRAL (combines relative
                     score with BTC's absolute direction — a falling BTC
                     dominance is NOT an alt signal when BTC just fell
                     faster, frontier notes §10)
    long_candidates  top-N symbols with score > 0 (empty when dispersion
                     gate trips)
    short_candidates bottom symbol only, score < 0 AND higher-timeframe
                     downtrend (8h momentum < 0 and EMA cross BEAR)
    """
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items()})
    syms = [s for s, f in features_by_symbol.items() if f]
    if not syms:
        return {"ranking": [], "scores": {}, "factor_z": {}, "dispersion": 0.0,
                "rotation": "NEUTRAL", "long_candidates": [],
                "short_candidates": []}

    def field(name: str) -> dict[str, float]:
        return {s: float(features_by_symbol[s].get(name) or 0.0) for s in syms}

    mom8 = field("ret_16bar_pct")
    z_mom8 = _zscores(mom8)
    z_mom24 = _zscores(field("ret_48bar_pct"))
    z_trend = _zscores(field("ema_dist_pct"))

    vol_raw: dict[str, float] = {}
    for s in syms:
        f = features_by_symbol[s]
        vz = f.get("volume_z_deseason")
        if vz is None:
            vz = f.get("volume_z_48bar")
        vz = float(vz or 0.0)
        # sign the volume confirmation by the 8h momentum direction:
        # abnormal volume WITH the move confirms it, against it warns
        vol_raw[s] = max(-3.0, min(vz, 3.0)) * (1.0 if mom8[s] >= 0 else -1.0)
    z_vol = _zscores(vol_raw)

    funding_vals = {s: float((funding or {}).get(s)
                             or features_by_symbol[s].get("funding_rate") or 0.0)
                    for s in syms}
    z_funding = _zscores(funding_vals)

    scores: dict[str, float] = {}
    for s in syms:
        scores[s] = round(
            w["mom_8h"] * z_mom8[s]
            + w["mom_24h"] * z_mom24[s]
            + w["trend_quality"] * z_trend[s]
            + w["volume_confirm"] * z_vol[s]
            # penalty only: crowded longs (funding far above peers) get
            # marked down; negative funding z never adds points
            - w["funding_crowding"] * max(z_funding[s], 0.0),
            4)

    ranking = sorted(syms, key=lambda s: scores[s], reverse=True)
    n = len(scores)
    mu = sum(scores.values()) / n
    dispersion = round((sum((v - mu) ** 2 for v in scores.values()) / n) ** 0.5, 4)

    rotation = "NEUTRAL"
    if "BTC" in scores and n >= 3:
        alt = [v for s, v in scores.items() if s != "BTC"]
        alt_mean = sum(alt) / len(alt)
        if alt_mean - scores["BTC"] > 0.5 and mom8.get("BTC", 0) > 0:
            rotation = "ALT_FAVOR"   # alts relatively stronger, BTC not falling
        elif scores["BTC"] - alt_mean > 0.5:
            rotation = "BTC_FAVOR"

    gate_open = dispersion >= min_dispersion
    long_candidates = [s for s in ranking[:max(top_n, 0)]
                       if scores[s] > 0] if gate_open else []
    short_candidates = []
    if gate_open and ranking:
        worst = ranking[-1]
        f = features_by_symbol[worst]
        if scores[worst] < 0 and mom8[worst] < 0 \
                and f.get("ema_cross") == "BEAR":
            short_candidates = [worst]

    return {
        "ranking": ranking,
        "scores": scores,
        "factor_z": {"mom_8h": z_mom8, "mom_24h": z_mom24,
                     "trend_quality": z_trend, "volume_confirm": z_vol,
                     "funding": z_funding},
        "dispersion": dispersion,
        "rotation": rotation,
        "long_candidates": long_candidates,
        "short_candidates": short_candidates,
    }
