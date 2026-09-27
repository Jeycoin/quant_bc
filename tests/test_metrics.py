"""Tests for the pure-metric functions in analytics/metrics.py."""

from analytics import metrics as m


def _trade(pnl, open_ts=1000.0, close_ts=4600.0, fees=0.01):
    return {
        "pnl_quote": pnl, "ts_open": open_ts, "ts_close": close_ts,
        "fees_quote": fees,
    }


def test_trading_metrics_basic():
    trades = [_trade(10.0), _trade(-4.0), _trade(6.0),
              {"pnl_quote": None, "ts_open": 1.0, "ts_close": None}]  # open trade
    r = m.trading_metrics(trades)
    assert r["trade_count"] == 4
    assert r["closed_count"] == 3
    assert r["total_pnl_quote"] == 12.0
    assert r["win_rate"] == 2 / 3
    assert r["avg_win"] == 8.0
    assert r["avg_loss"] == -4.0
    assert r["profit_factor"] == 16.0 / 4.0
    assert r["expectancy"] == 4.0
    assert r["avg_holding_s"] == 3600.0
    assert r["total_fees_quote"] == 0.03


def test_trading_metrics_empty():
    r = m.trading_metrics([])
    assert r["trade_count"] == 0
    assert r["win_rate"] is None
    assert r["profit_factor"] is None


def test_equity_metrics_drawdown():
    snaps = [
        {"ts": 0.0, "equity": 100.0},
        {"ts": 3600.0, "equity": 120.0},
        {"ts": 7200.0, "equity": 90.0},   # 25% drawdown from peak 120
        {"ts": 86400.0, "equity": 110.0},
    ]
    r = m.equity_metrics(snaps)
    assert r["total_return"] == 0.10
    assert abs(r["max_drawdown_pct"] - 25.0) < 1e-9
    assert r["daily_return"] == 0.10  # span exactly one day
    assert r["snapshot_count"] == 4


def test_equity_metrics_insufficient_data():
    r = m.equity_metrics([{"ts": 0.0, "equity": 100.0}])
    assert r["total_return"] is None
    assert r["max_drawdown_pct"] is None


def test_execution_metrics():
    orders = [
        {"status": "FILLED", "price": 100.0, "average_fill_price": 100.1},
        {"status": "FILLED", "price": 100.0, "average_fill_price": 99.9},
        {"status": "CANCELED", "price": 100.0, "average_fill_price": None},
        {"status": "FAILED", "price": None, "average_fill_price": None},
    ]
    r = m.execution_metrics(orders)
    assert r["order_count"] == 4
    assert r["fill_rate"] == 0.5
    assert r["cancel_rate"] == 0.25
    assert r["failure_rate"] == 0.25
    assert abs(r["avg_slippage_pct"] - 0.1) < 1e-9


def test_exposure_metrics():
    positions = [
        {"trading_pair": "BTC-USDT", "amount": 0.01, "entry_price": 80000.0},
        {"trading_pair": "ETH-USDT", "amount": 0.1, "entry_price": 2000.0},
    ]
    r = m.exposure_metrics(positions, equity=10000.0)
    assert r["gross_exposure"] == 1000.0
    assert r["exposure_pct"] == 10.0
    assert r["position_concentration"] == 0.8
    assert set(r["by_symbol"]) == {"BTC-USDT", "ETH-USDT"}
