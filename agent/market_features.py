"""Timeframe-aligned market features, shared by the live agent and replay.

A decision loop running every N minutes must see factors computed on
matching bars — fresh every cycle — instead of slow daily statistics that
repeat unchanged for a whole day. This module derives multi-horizon
features from a plain candle list; the live agent (30m candles via the
Hummingbot REST API) and the historical replay (30m replayed candles) both
call it, so the LLM sees identical inputs in research and in production.

Horizon guide at the default 30m bar size: 1 bar = 30m, 2 = 1h, 4 = 2h,
8 = 4h, 16 = 8h, 48 = 24h. Fast horizons (1-8 bars) are the short-term
signal; the 48-bar (24h) fields are slow background context.
"""

from __future__ import annotations

from typing import Any

MIN_BARS = 50  # need >= 48 bars for the 24h window plus EMA warm-up
DESEASON_MIN_DAYS = 4   # need >= 4 days of bars for a time-of-day baseline
DESEASON_DAYS = 7       # trailing days used for the volume profile


def _deseasonalized_volume_z(candles: list[dict], bar_minutes: int) -> float | None:
    """Volume z-score after removing the intraday seasonality profile.

    Crypto volume has a strong U-shaped time-of-day pattern (US hours busy,
    Asia late night thin — see docs/research/crypto-quant-frontier-notes.md
    §11). A plain 24h z-score therefore overstates anomalies in quiet hours
    and understates them in busy hours. Here each bar's volume is divided by
    its time-of-day profile factor (trailing DESEASON_DAYS mean at the same
    slot / trailing global mean), then z-scored against the trailing 48
    adjusted bars — the same window as the raw z for comparability.

    Returns None when history is insufficient (< DESEASON_MIN_DAYS days);
    callers must fall back to the raw volume_z_48bar.
    """
    if bar_minutes <= 0 or 1440 % bar_minutes != 0:
        return None
    slots = 1440 // bar_minutes
    need = slots * DESEASON_MIN_DAYS
    if len(candles) < need + 49:
        return None
    hist = candles[-(slots * DESEASON_DAYS + 49):]
    bar_s = bar_minutes * 60
    slot_sum = [0.0] * slots
    slot_n = [0] * slots
    for c in hist[:-1]:  # profile excludes the current bar
        slot = int(c["timestamp"] // bar_s) % slots
        slot_sum[slot] += float(c.get("volume", 0) or 0)
        slot_n[slot] += 1
    global_mean = sum(slot_sum) / max(sum(slot_n), 1)
    if global_mean <= 0:
        return None

    def factor(ts: float) -> float:
        slot = int(ts // bar_s) % slots
        mean = slot_sum[slot] / slot_n[slot] if slot_n[slot] else global_mean
        f = mean / global_mean if global_mean else 1.0
        return min(max(f, 0.1), 10.0)  # clamp: thin slots must not explode

    adj = [float(c.get("volume", 0) or 0) / factor(c["timestamp"]) for c in hist]
    base = adj[-49:-1]
    mu = sum(base) / len(base)
    sd = (sum((v - mu) ** 2 for v in base) / len(base)) ** 0.5
    if sd <= max(mu, 1.0) * 1e-9:  # float noise, not real variance
        return 0.0 if adj[-1] <= mu else 10.0
    return round(min((adj[-1] - mu) / sd, 10.0), 2)


def _ema(values: list[float], n: int) -> float:
    k = 2 / (n + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def _rsi(closes: list[float], n: int = 14) -> float:
    gains = 0.0
    losses = 0.0
    for i in range(-n, 0):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + (gains / n) / (losses / n))


def _atr_pct(candles: list[dict], n: int = 14) -> float:
    trs = []
    for i in range(-n, 0):
        h = float(candles[i]["high"])
        lo = float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    price = float(candles[-1]["close"])
    return (sum(trs) / n) / price * 100 if price else 0.0


def compute_market_features(
    candles: list[dict],
    upto_ts: float | None = None,
    funding_rate: float | None = None,
    bar_minutes: int | None = None,
) -> dict[str, Any] | None:
    """Multi-horizon features from ascending {timestamp, open, high, low,
    close, volume} candles. Returns None when history is insufficient."""
    past = [c for c in candles if upto_ts is None or c["timestamp"] <= upto_ts]
    if len(past) < MIN_BARS:
        return None
    closes = [float(c["close"]) for c in past]
    vols = [float(c["volume"]) for c in past]
    price = closes[-1]
    if bar_minutes is None:
        delta = past[-1]["timestamp"] - past[-2]["timestamp"]
        bar_minutes = max(1, round(delta / 60))

    def ret(n: int) -> float | None:
        if len(closes) <= n:
            return None
        return round((closes[-1] / closes[-1 - n] - 1) * 100, 3)

    def window(n: int) -> tuple[float, float]:
        w = past[-n:]
        return max(float(c["high"]) for c in w), min(float(c["low"]) for c in w)

    def pos_in_range(hi: float, lo: float) -> float:
        return round((price - lo) / (hi - lo), 3) if hi > lo else 0.5

    def rvol(n: int) -> float:
        rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(-n, 0)]
        mu = sum(rets) / len(rets)
        return round((sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5 * 100, 3)

    hi8, lo8 = window(8)
    hi48, lo48 = window(48)

    base = vols[-49:-1]
    mu = sum(base) / len(base)
    sd = (sum((v - mu) ** 2 for v in base) / len(base)) ** 0.5
    if sd > 0:
        volume_z = round(min((vols[-1] - mu) / sd, 10.0), 2)
    else:
        # zero-variance baseline: any deviation is maximally abnormal
        volume_z = 0.0 if vols[-1] <= mu else 10.0

    ema_fast = _ema(closes[-32:], 8)
    ema_slow = _ema(closes, 21)

    return {
        "bar_minutes": bar_minutes,
        "asof_ts": past[-1]["timestamp"],
        "price": price,
        # returns over 1/2/4/8/16/48 bars (30m/1h/2h/4h/8h/24h at 30m bars)
        "ret_1bar_pct": ret(1),
        "ret_2bar_pct": ret(2),
        "ret_4bar_pct": ret(4),
        "ret_8bar_pct": ret(8),
        "ret_16bar_pct": ret(16),
        "ret_48bar_pct": ret(48),
        "range_8bar": {"high": hi8, "low": lo8, "pos": pos_in_range(hi8, lo8)},
        "range_48bar": {"high": hi48, "low": lo48, "pos": pos_in_range(hi48, lo48)},
        "realized_vol_8bar_pct": rvol(8),
        "realized_vol_48bar_pct": rvol(48),
        # current bar volume vs its trailing 48-bar distribution;
        # |z| > 2 flags abnormal activity (real vs fake breakout confirmation)
        "volume_z_48bar": volume_z,
        # time-of-day deseasonalized version (None until >= 4d of history);
        # prefer this for breakout confirmation when available — the raw z
        # misjudges quiet/busy hours (frontier notes §11)
        "volume_z_deseason": _deseasonalized_volume_z(past, bar_minutes),
        "ema_fast_8bar": round(ema_fast, 2),
        "ema_slow_21bar": round(ema_slow, 2),
        "ema_cross": "BULL" if ema_fast > ema_slow else "BEAR",
        "ema_dist_pct": round((ema_fast / ema_slow - 1) * 100, 3),
        "rsi_14bar": round(_rsi(closes), 1),
        "atr_14bar_pct": round(_atr_pct(past), 3),
        # slow field: funding updates only every ~8h
        "funding_rate": funding_rate,
    }
