"""Tests for analytics/performance.py (daily series + bootstrap CIs)."""

import math

from analytics import performance as p

DAY = p.DAY_S


def _trade(pnl, close_ts, fees=0.01, open_ts=None):
    return {
        "pnl_quote": pnl, "ts_open": open_ts if open_ts is not None else close_ts - 3600,
        "ts_close": close_ts, "fees_quote": fees,
    }


def test_daily_pnl_series_zero_fills_empty_days():
    trades = [
        _trade(10.0, DAY * 2 + 100),   # day 2
        _trade(-4.0, DAY * 2 + 200),   # day 2
        _trade(5.0, DAY * 5 + 100),    # day 5
    ]
    days = p.daily_pnl_series(trades, start_ts=DAY * 2, end_ts=DAY * 5 + 3600)
    assert len(days) == 4
    assert days[0]["pnl"] == 6.0 and days[0]["trades"] == 2
    assert days[0]["wins"] == 1
    assert days[1]["pnl"] == 0.0 and days[1]["trades"] == 0  # zero-filled
    assert days[3]["pnl"] == 5.0
    assert abs(days[0]["fees"] - 0.02) < 1e-12


def test_daily_pnl_series_empty():
    assert p.daily_pnl_series([]) == []
    assert p.daily_pnl_series([{"pnl_quote": None, "ts_close": None}]) == []


def test_daily_metrics_known_values():
    # 4 days: +10, -20, +30, -5 on 1000 reference equity
    days = [{"day_ts": i * DAY, "pnl": v, "fees": 0.0, "trades": 1, "wins": int(v > 0)}
            for i, v in enumerate([10.0, -20.0, 30.0, -5.0])]
    r = p.daily_metrics(days, reference_equity=1000.0)
    assert r["n_days"] == 4
    assert r["total_pnl"] == 15.0
    assert r["total_return_pct"] == 1.5
    # cumulative: 10, -10, 20, 15 -> peak 20 (after day3 cum=20), max dd:
    # day2: peak 10, dd 20; day3: peak 20; day4: dd 5 -> max 20 => 2%
    assert r["max_drawdown_pct"] == 2.0
    assert r["worst_day_pct"] == -2.0
    # CVaR-95 over 4 obs -> k = ceil(0.2) = 1 -> worst day only
    assert r["cvar95_daily_pct"] == -2.0
    assert r["sharpe_daily"] is not None
    assert r["sortino_daily"] is not None
    assert r["calmar"] == 0.75  # 1.5% / 2%


def test_daily_metrics_all_winning_no_downside():
    days = [{"day_ts": i * DAY, "pnl": 10.0, "fees": 0.0, "trades": 1, "wins": 1}
            for i in range(5)]
    r = p.daily_metrics(days, reference_equity=1000.0)
    assert r["volatility_daily_pct"] == 0.0
    assert r["sharpe_daily"] is None          # zero variance -> undefined
    assert r["sortino_daily"] is None         # no downside deviation
    assert r["calmar"] is None                # no drawdown
    assert r["max_drawdown_pct"] == 0.0


def test_daily_metrics_empty():
    assert p.daily_metrics([]) == {"n_days": 0}


def test_moving_block_bootstrap_shape_and_seed():
    values = list(range(30))
    a = p.moving_block_bootstrap(values, block_len=3, n_resamples=10, seed=7)
    b = p.moving_block_bootstrap(values, block_len=3, n_resamples=10, seed=7)
    assert len(a) == 10 and all(len(s) == 30 for s in a)
    assert a == b  # fixed seed -> reproducible
    # every element must come from the original series
    assert all(v in values for s in a for v in s)


def test_moving_block_bootstrap_block_len_clamped():
    values = [1.0, 2.0]
    out = p.moving_block_bootstrap(values, block_len=99, n_resamples=3, seed=1)
    assert all(s == [1.0, 2.0] for s in out)  # block covers the whole series
    assert p.moving_block_bootstrap([], 3, 5) == []


def test_bootstrap_ci_recovers_mean_of_stationary_series():
    # constant series: every resample identical -> CI collapses to the point
    values = [5.0] * 30
    r = p.bootstrap_ci(values, lambda s: sum(s), block_len=3,
                       n_resamples=100, seed=1)
    assert r["estimate"] == 150.0
    assert r["ci_lo"] == 150.0 and r["ci_hi"] == 150.0
    assert r["n_resamples"] == 100


def test_bootstrap_ci_brackets_estimate():
    values = [3.0, -1.0, 2.0, 0.5, -2.0, 4.0, 1.0, -0.5] * 5
    r = p.bootstrap_ci(values, lambda s: sum(s) / len(s), block_len=3,
                       n_resamples=500, seed=11)
    assert r["ci_lo"] <= r["estimate"] <= r["ci_hi"]
    assert r["block_len"] == 3


def test_bootstrap_ci_degenerate_inputs():
    r = p.bootstrap_ci([], lambda s: 1.0)
    assert r["ci_lo"] is None
    r = p.bootstrap_ci([1.0], lambda s: None)
    assert r["estimate"] is None and r["ci_lo"] is None
