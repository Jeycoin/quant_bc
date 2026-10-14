"""Unit tests for the composite fitness scorer (v1.4)."""

from analytics.fitness import composite_fitness


def _trade(pnl, ts_open, ts_close, fees=0.1):
    return {"symbol": "BTC", "side": "LONG", "pnl_quote": pnl,
            "fees_quote": fees, "gross_pnl": pnl + fees,
            "ts_open": ts_open, "ts_close": ts_close, "status": "CLOSED"}


def _steady_winners():
    # 8 winners spread evenly across 8 days -> consistent, low DD
    return [_trade(10.0, ts_open=d * 86400, ts_close=d * 86400 + 3600)
            for d in range(8)]


def _one_window_lucky():
    # same total PnL but all wins in a single day, then losses
    trades = [_trade(20.0, ts_open=3600 * i, ts_close=3600 * (i + 1))
              for i in range(4)]
    trades += [_trade(-1.0, ts_open=10 * 86400 + 3600 * i,
                      ts_close=10 * 86400 + 3600 * (i + 1)) for i in range(4)]
    return trades


def test_steady_winners_positive_fitness():
    fit = composite_fitness(_steady_winners())
    assert fit["fitness"] is not None
    assert fit["fitness"] > 0
    assert fit["components"]["window_consistency"] == 1.0


def test_consistency_distinguishes_lucky_window():
    steady = composite_fitness(_steady_winners())
    lucky = composite_fitness(_one_window_lucky())
    assert steady["components"]["window_consistency"] > \
        lucky["components"]["window_consistency"]


def test_losing_strategy_negative_fitness():
    trades = [_trade(-5.0, ts_open=d * 86400, ts_close=d * 86400 + 3600)
              for d in range(8)]
    fit = composite_fitness(trades)
    assert fit["fitness"] < 0


def test_fee_churn_penalized():
    # gross positive but fees eat most of it
    trades = [_trade(0.05, ts_open=d * 86400, ts_close=d * 86400 + 3600,
                     fees=0.5) for d in range(8)]
    fit = composite_fitness(trades)
    assert fit["components"]["fee_ratio"] > 0.8
    assert fit["components"]["turnover_penalty"] < 0.2


def test_empty_trades():
    fit = composite_fitness([])
    assert fit["fitness"] is None
