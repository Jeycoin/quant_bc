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


def _adx(candles: list[dict], n: int = 14) -> float | None:
    """Average Directional Index — trend STRENGTH (0-100), direction-free.

    < 20 = chop / no trend, 20-25 = emerging, > 25 = trending. The single
    most important factor for the grid-vs-trend strategy choice.
    """
    if len(candles) < 2 * n + 2:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(candles)):
        h, ph = float(candles[i]["high"]), float(candles[i - 1]["high"])
        lo, plo = float(candles[i]["low"]), float(candles[i - 1]["low"])
        pc = float(candles[i - 1]["close"])
        up, dn = h - ph, plo - lo
        plus_dm.append(up if up > dn and up > 0 else 0.0)
        minus_dm.append(dn if dn > up and dn > 0 else 0.0)
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))

    def smooth(xs: list[float]) -> list[float]:
        out = [sum(xs[:n])]
        for x in xs[n:]:
            out.append(out[-1] - out[-1] / n + x)
        return out

    tr_s, p_s, m_s = smooth(trs), smooth(plus_dm), smooth(minus_dm)
    dxs = []
    for t, p, m in zip(tr_s, p_s, m_s):
        if t <= 0:
            continue
        pdi, mdi = 100 * p / t, 100 * m / t
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi > 0 else 0.0)
    if len(dxs) < n:
        return None
    return round(sum(dxs[-n:]) / n, 1)


def _streak(closes: list[float]) -> int:
    """Consecutive up (+) or down (-) bars at the tail. |streak| >= 4 at
    30m bars is exhaustion territory for mean-reversion, fuel for momentum."""
    s = 0
    for i in range(len(closes) - 1, 0, -1):
        d = closes[i] - closes[i - 1]
        if d == 0:
            break
        sign = 1 if d > 0 else -1
        if s == 0:
            s = sign
        elif (s > 0) != (sign > 0):
            break
        else:
            s += sign
    return s


def _vwap_dist_pct(candles: list[dict], n: int = 48) -> float | None:
    """Distance from the trailing n-bar VWAP — the intraday fair-value
    anchor. Price stretched > ~1.5x ATR above VWAP = poor chase entry."""
    w = candles[-n:]
    pv = sum(float(c["close"]) * float(c.get("volume", 0) or 0) for c in w)
    vv = sum(float(c.get("volume", 0) or 0) for c in w)
    if vv <= 0:
        return None
    vwap = pv / vv
    price = float(candles[-1]["close"])
    return round((price / vwap - 1) * 100, 3) if vwap else None


def _ts_rank(values: list[float]) -> float | None:
    """Rank of the latest value within the series (0..1). High = the latest
    point sits at the top of its own trailing window."""
    if len(values) < 5:
        return None
    last = values[-1]
    below = sum(1 for v in values if v <= last)
    return round(below / len(values), 2)


def _choppiness(candles: list[dict], n: int = 14) -> float | None:
    """Choppiness Index: 100*log10(sum(ATR,n)/(maxHigh-minLow))/log10(n).
    >61.8 = chop, <38.2 = trending. Direction-agnostic regime measure —
    complements ADX/efficiency ratio as a third opinion."""
    if len(candles) < n + 1:
        return None
    import math
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    trs = [max(highs[i] - lows[i],
               abs(highs[i] - closes[i - 1]),
               abs(lows[i] - closes[i - 1]))
           for i in range(1, len(candles))]
    atr_sum = sum(trs[-n:])
    hi = max(highs[-n:])
    lo = min(lows[-n:])
    if hi <= lo or atr_sum <= 0:
        return None
    return round(100 * math.log10(atr_sum / (hi - lo)) / math.log10(n), 1)


def _corr(a: list[float], b: list[float]) -> float | None:
    """Correlation of RETURNS (pct changes) — for cross-asset factors."""
    n = min(len(a), len(b))
    if n < 10:
        return None
    ra = [(a[i] / a[i - 1] - 1) for i in range(-n + 1, 0) if a[i - 1]]
    rb = [(b[i] / b[i - 1] - 1) for i in range(-n + 1, 0) if b[i - 1]]
    m = min(len(ra), len(rb))
    if m < 10:
        return None
    ra, rb = ra[-m:], rb[-m:]
    return _pearson(ra, rb)


def _pearson(a: list[float], b: list[float]) -> float | None:
    """Plain Pearson correlation of levels/ranks."""
    m = min(len(a), len(b))
    if m < 5:
        return None
    a, b = a[-m:], b[-m:]
    ma, mb = sum(a) / m, sum(b) / m
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return round(cov / (va * vb) ** 0.5, 2)


def compute_market_features(
    candles: list[dict],
    upto_ts: float | None = None,
    funding_rate: float | None = None,
    bar_minutes: int | None = None,
    ref_candles: list[dict] | None = None,
    funding_history: list[float] | None = None,
) -> dict[str, Any] | None:
    """Multi-horizon features from ascending {timestamp, open, high, low,
    close, volume} candles. Returns None when history is insufficient.

    ref_candles: benchmark (BTC) candles for cross-asset factors
    (correlation, relative strength, beta). Ignored for BTC itself.
    funding_history: recent funding rates ascending (~8h spacing) for
    funding momentum; current funding_rate should be its last point."""
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
    ema50 = _ema(closes[-96:], 50) if len(closes) >= 60 else None

    # MACD (12/26/9) histogram — momentum acceleration, in % of price
    macd_hist_pct = None
    if len(closes) >= 40:
        dif = _ema(closes, 12) - _ema(closes, 26)
        # DEA: 9-bar EMA of DIF over the trailing window
        difs = []
        for i in range(30, len(closes) + 1):
            w = closes[:i]
            difs.append(_ema(w, 12) - _ema(w, 26))
        dea = _ema(difs[-12:], 9)
        macd_hist_pct = round((dif - dea) / price * 100, 3) if price else None

    # Kaufman efficiency ratio over 16 bars: |net move| / path length.
    # ~1 = clean trend, ~0 = pure chop. The grid-vs-trend switch.
    er_16bar = None
    if len(closes) >= 17:
        path = sum(abs(closes[i] - closes[i - 1]) for i in range(-16, 0))
        net = abs(closes[-1] - closes[-17])
        er_16bar = round(net / path, 2) if path > 0 else None

    # Bollinger width (20-bar, 2sd) — squeeze detection
    bb_width_pct = None
    if len(closes) >= 20:
        w20 = closes[-20:]
        mu20 = sum(w20) / 20
        sd20 = (sum((v - mu20) ** 2 for v in w20) / 20) ** 0.5
        bb_width_pct = round(4 * sd20 / mu20 * 100, 3) if mu20 else None

    # ATR percentile vs its own trailing history (vol regime, not level)
    atr_pct_now = _atr_pct(past)
    atr_pctile = None
    if len(past) >= 64:
        atr_hist = [
            _atr_pct(past[:i]) for i in range(len(past) - 48, len(past) + 1)
        ]
        atr_hist = [a for a in atr_hist if a is not None]
        if atr_hist:
            below = sum(1 for a in atr_hist if a <= atr_pct_now)
            atr_pctile = round(below / len(atr_hist), 2)

    # up-bar volume share over 8 bars: 0.5 = balanced; > 0.65 = buyers
    # dominating volume (conviction), < 0.35 = distribution
    up_vol_share_8bar = None
    w8 = past[-8:]
    up_v = sum(float(c.get("volume", 0) or 0)
               for c in w8 if float(c["close"]) >= float(c["open"]))
    tot_v = sum(float(c.get("volume", 0) or 0) for c in w8)
    if tot_v > 0:
        up_vol_share_8bar = round(up_v / tot_v, 2)

    # bars since the 48-bar high — trend freshness (0 = making highs now)
    bars_since_high = 0
    for i in range(len(past) - 1, max(len(past) - 49, -1), -1):
        if float(past[i]["high"]) >= hi48 - 1e-12:
            bars_since_high = len(past) - 1 - i
            break

    # distance below the 24h range high (%): how much room to the ceiling
    dist_from_high_pct = round((price / hi48 - 1) * 100, 3) if hi48 else None

    # price path shape: % change vs first point of the last 24 bars, so
    # the LLM sees grind-up vs spike-and-fade (aggregates cannot tell)
    window24 = past[-24:]
    path_pct = None
    if window24 and window24[0]["close"]:
        base_px = float(window24[0]["close"])
        path_pct = [round((float(c["close"]) / base_px - 1) * 100, 2)
                    for c in window24]

    # higher-timeframe alignment: resample to 2h bars from the same
    # candles — 30m noise against a 2h trend is the classic losing fight
    htf: dict[str, Any] = {}
    factor = max(1, round(120 / bar_minutes))
    if len(past) >= factor * 30:
        groups = [past[i:i + factor]
                  for i in range(0, len(past) - len(past) % factor, factor)]
        htf_closes = [float(g[-1]["close"]) for g in groups]
        if len(htf_closes) >= 26:
            htf_fast = _ema(htf_closes[-16:], 8)
            htf_slow = _ema(htf_closes, 21)
            htf["ema_cross_2h"] = "BULL" if htf_fast > htf_slow else "BEAR"
            htf["ret_6h_pct"] = round(
                (htf_closes[-1] / htf_closes[-4] - 1) * 100, 3)
            htf["rsi_2h"] = round(_rsi(htf_closes), 1)

    # funding momentum: current funding vs its trailing distribution
    # (~7d at 8h spacing). Extreme positive funding = crowded longs.
    funding_z = None
    if funding_history and len(funding_history) >= 5:
        mu_f = sum(funding_history) / len(funding_history)
        sd_f = (sum((f - mu_f) ** 2 for f in funding_history)
                / len(funding_history)) ** 0.5
        cur_f = funding_rate if funding_rate is not None else funding_history[-1]
        if sd_f > 1e-12:
            funding_z = round(min(max((cur_f - mu_f) / sd_f, -10.0), 10.0), 2)

    # --- v1.4 factors (borrowed from Phandas/Freqtrade/GP-mining evidence) --

    # short-term reversal rank: where the latest n-bar return sits inside its
    # own trailing 48-window distribution. >0.8 = overextended up (fade
    # candidate), <0.2 = overextended down. Crypto's most robust anomaly is
    # short-horizon reversal; pure momentum factors cannot express it.
    ret_rank_4bar = None
    ret_rank_8bar = None
    if len(closes) >= 53:
        rets4 = [closes[i] / closes[i - 4] - 1 for i in range(52, len(closes))]
        rets8 = [closes[i] / closes[i - 8] - 1 for i in range(52, len(closes))]
        ret_rank_4bar = _ts_rank(rets4[-48:])
        ret_rank_8bar = _ts_rank(rets8[-48:])

    # price-volume divergence (Alpha101 #22 family): correlation of
    # rank(close) vs rank(volume) over 12 bars. Negative = price rising on
    # shrinking volume (or falling on expanding) — trend losing its fuel.
    pv_corr_12bar = None
    if len(closes) >= 17:
        w_c = closes[-12:]
        w_v = vols[-12:]

        def _ranks(xs: list[float]) -> list[float]:
            order = sorted(range(len(xs)), key=lambda i: xs[i])
            r = [0.0] * len(xs)
            for pos, idx in enumerate(order):
                r[idx] = float(pos)
            return r

        pv_corr_12bar = _pearson(_ranks(w_c), _ranks(w_v))

    # EWO (Elliott Wave Oscillator, Freqtrade NFI/ElliotV2 core): normalized
    # EMA5-EMA35 difference — slower than our 8/21 cross, the community's
    # standard dip-buying momentum gauge on 30m crypto.
    ewo_pct = None
    if len(closes) >= 40:
        ewo_pct = round((_ema(closes, 5) - _ema(closes, 35)) / price * 100, 3) \
            if price else None

    chop_14bar = _choppiness(past)

    # liquidation-cascade proxy: last-bar wick anatomy + volume spike.
    # Long lower wick on a volume surge = forced sellers absorbed
    # (capitulation, bounce candidate); long upper wick = distribution.
    upper_wick_pct = lower_wick_pct = None
    cascade_signal = None
    last = past[-1]
    o, h, l, c = (float(last["open"]), float(last["high"]),
                  float(last["low"]), float(last["close"]))
    bar_range = h - l
    if bar_range > 0:
        upper_wick_pct = round((h - max(o, c)) / bar_range * 100, 1)
        lower_wick_pct = round((min(o, c) - l) / bar_range * 100, 1)
        if volume_z is not None and volume_z >= 2.0:
            if lower_wick_pct >= 50:
                cascade_signal = "CAPITULATION"
            elif upper_wick_pct >= 50:
                cascade_signal = "DISTRIBUTION"

    out: dict[str, Any] = {
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
        "ema50_dist_pct": (
            round((price / ema50 - 1) * 100, 3) if ema50 else None),
        "adx_14bar": _adx(past),
        "macd_hist_pct": macd_hist_pct,
        "efficiency_ratio_16bar": er_16bar,
        "bb_width_pct": bb_width_pct,
        "atr_pctile_48bar": atr_pctile,
        "streak_bars": _streak(closes),
        "up_vol_share_8bar": up_vol_share_8bar,
        "vwap_dist_48bar_pct": _vwap_dist_pct(past),
        "bars_since_48bar_high": bars_since_high,
        "dist_from_48bar_high_pct": dist_from_high_pct,
        "rsi_14bar": round(_rsi(closes), 1),
        "rsi_slope_4bar": (
            round(_rsi(closes) - _rsi(closes[:-4]), 1)
            if len(closes) >= 19 else None),
        "atr_14bar_pct": round(atr_pct_now, 3),
        # slow field: funding updates only every ~8h
        "funding_rate": funding_rate,
        "funding_z_7d": funding_z,
        "ret_rank_4bar": ret_rank_4bar,
        "ret_rank_8bar": ret_rank_8bar,
        "pv_corr_12bar": pv_corr_12bar,
        "ewo_pct": ewo_pct,
        "chop_14bar": chop_14bar,
        "upper_wick_pct": upper_wick_pct,
        "lower_wick_pct": lower_wick_pct,
        "cascade_signal": cascade_signal,
        "path_pct": path_pct,
        "htf_2h": htf or None,
    }

    # cross-asset factors vs the benchmark (BTC): alt moves are mostly
    # beta to BTC — the LLM needs to know whether THIS symbol is leading
    # or just following
    if ref_candles:
        ref_past = [c for c in ref_candles
                    if upto_ts is None or c["timestamp"] <= upto_ts]
        ref_closes = [float(c["close"]) for c in ref_past]
        if len(ref_closes) >= 50:
            out["corr_btc_48bar"] = _corr(closes[-49:], ref_closes[-49:])
            if len(closes) >= 17 and len(ref_closes) >= 17:
                rs = (closes[-1] / closes[-17] - 1) - (
                    ref_closes[-1] / ref_closes[-17] - 1)
                out["rs_vs_btc_8h_pct"] = round(rs * 100, 2)
    return out
