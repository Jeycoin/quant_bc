"""Unit tests for the timeframe-aligned market feature engine."""

import time

from agent.market_features import MIN_BARS, compute_market_features


def _candles(closes, volumes=None, start_ts=1_700_000_000, bar_s=1800):
    volumes = volumes or [100.0] * len(closes)
    out = []
    for i, (c, v) in enumerate(zip(closes, volumes)):
        out.append({
            "timestamp": start_ts + i * bar_s,
            "open": c, "high": c * 1.001, "low": c * 0.999,
            "close": c, "volume": v,
        })
    return out


def test_insufficient_history_returns_none():
    assert compute_market_features(_candles([100.0] * 20)) is None


def test_flat_market_features():
    f = compute_market_features(_candles([100.0] * 60))
    assert f["price"] == 100.0
    assert f["ret_1bar_pct"] == 0.0
    assert f["ret_48bar_pct"] == 0.0
    assert f["range_48bar"]["pos"] == 0.5
    assert abs(f["volume_z_48bar"]) < 0.1
    assert abs(f["ema_dist_pct"]) < 1e-6  # flat: fast ≈ slow (float noise)
    assert f["bar_minutes"] == 30


def test_uptrend_fast_signals_positive():
    closes = [100.0] * 52 + [101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0]
    f = compute_market_features(_candles(closes))
    assert f["ret_1bar_pct"] > 0
    assert f["ret_8bar_pct"] > 5.0
    assert f["ema_cross"] == "BULL"
    assert f["ema_dist_pct"] > 0
    assert f["rsi_14bar"] == 100.0  # no down bar in the RSI window
    assert f["range_8bar"]["pos"] > 0.95


def test_volume_spike_zscore():
    volumes = [100.0] * 59 + [1000.0]
    f = compute_market_features(_candles([100.0] * 60, volumes))
    assert f["volume_z_48bar"] > 5


def test_upto_ts_truncates_history():
    candles = _candles([100.0] * 60 + [200.0] * 30)
    cutoff = candles[59]["timestamp"]  # last bar of the flat region
    f = compute_market_features(candles, upto_ts=cutoff)
    assert f is not None
    assert f["price"] == 100.0


def test_bar_minutes_explicit_override():
    candles = _candles([100.0] * 60, bar_s=900)
    f = compute_market_features(candles)
    assert f["bar_minutes"] == 15
    f2 = compute_market_features(candles, bar_minutes=5)
    assert f2["bar_minutes"] == 5


def test_min_bars_boundary():
    assert compute_market_features(_candles([100.0] * (MIN_BARS - 1))) is None
    assert compute_market_features(_candles([100.0] * MIN_BARS)) is not None


def test_deseason_volume_none_on_short_history():
    # 60 bars = 30h at 30m: far below the 4-day minimum -> None
    f = compute_market_features(_candles([100.0] * 60))
    assert f["volume_z_deseason"] is None


def test_deseason_volume_corrects_time_of_day_bias():
    # 8 days at 30m. Every day: volume 100 except a recurring 1000 spike at
    # slot 10 (05:00 UTC). A same-slot spike is SEASONAL, not abnormal:
    # the deseasonalized z must stay low even when the current bar is 1000.
    bar_s = 1800
    slots = 48
    days = 8
    volumes = []
    for d in range(days):
        for s in range(slots):
            volumes.append(1000.0 if s == 10 else 100.0)
    # align start to a bar boundary so slot(i) == i % 48 exactly
    candles = _candles([100.0] * len(volumes), volumes,
                       start_ts=1_700_006_400, bar_s=bar_s)
    # end exactly on the seasonal spike slot
    while int(candles[-1]["timestamp"] // bar_s) % slots != 10:
        candles = candles[:-1]
        volumes = volumes[:-1]
    f = compute_market_features(candles)
    assert f is not None
    assert f["volume_z_48bar"] > 5          # raw z screams "abnormal"
    assert f["volume_z_deseason"] is not None
    assert abs(f["volume_z_deseason"]) < 2  # deseasonalized: just Tuesday
