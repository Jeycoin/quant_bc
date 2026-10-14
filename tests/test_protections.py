"""Unit tests for the post-hoc protections layer (v1.4)."""

import time

from agent.protections import evaluate_protections

NOW = time.time()
CFG = {
    "protections": {
        "enabled": True,
        "cooldown_hours": 0,
        "cooldown_stop_hours": 6,
        "stoploss_guard": {"enabled": True, "lookback_hours": 24,
                           "trade_limit": 4, "stop_duration_hours": 6},
        "max_drawdown": {"enabled": True, "lookback_hours": 72,
                         "max_allowed_pct": 3, "stop_duration_hours": 12},
        "low_profit_pairs": {"enabled": True, "lookback_hours": 168,
                             "min_trades": 4, "required_profit_quote": 0,
                             "stop_duration_hours": 72},
    }
}


def _trade(symbol="BTC", side="LONG", pnl=-1.0, close_type="STOP_LOSS",
           age_h=1.0):
    return {"symbol": symbol, "side": side, "pnl_quote": pnl,
            "close_type": close_type, "ts_close": NOW - age_h * 3600}


def test_empty_history_locks_nothing():
    rep = evaluate_protections([], NOW, config=CFG)
    assert not rep.global_lock
    assert rep.blocks("BTC", "LONG") is None


def test_disabled_config_locks_nothing():
    rep = evaluate_protections([_trade()], NOW,
                               config={"protections": {"enabled": False}})
    assert rep.blocks("BTC", "LONG") is None


def test_stop_cooldown_blocks_same_symbol_side():
    rep = evaluate_protections([_trade(age_h=2)], NOW, config=CFG)
    assert rep.blocks("BTC", "LONG") is not None
    # other symbol unaffected
    assert rep.blocks("ETH", "LONG") is None


def test_cooldown_expires():
    rep = evaluate_protections([_trade(age_h=7)], NOW, config=CFG)
    assert rep.blocks("BTC", "LONG") is None


def test_stoploss_guard_global_lock():
    trades = [_trade(symbol=s, age_h=2) for s in ("BTC", "ETH", "SOL", "XRP")]
    rep = evaluate_protections(trades, NOW, config=CFG)
    assert rep.global_lock
    assert rep.blocks("SUI", "SHORT") is not None


def test_stoploss_guard_needs_limit():
    trades = [_trade(symbol=s, age_h=2) for s in ("BTC", "ETH", "SOL")]
    rep = evaluate_protections(trades, NOW, config=CFG)
    assert not rep.global_lock


def test_max_drawdown_locks_globally():
    # -400 USDT realized drawdown on 10k reference = 4% > 3%
    trades = [_trade(symbol="BTC", pnl=-100, age_h=a)
              for a in (10, 8, 6, 4)]
    rep = evaluate_protections(trades, NOW, config=CFG)
    assert rep.global_lock
    assert any("drawdown" in r for r in rep.reasons)


def test_low_profit_pairs_blacklists_symbol():
    trades = [_trade(symbol="SUI", pnl=-2, close_type="TIME_STOP", age_h=a)
              for a in (100, 80, 60, 40)]
    rep = evaluate_protections(trades, NOW, config=CFG)
    assert rep.blocks("SUI") is not None
    assert "low profit" in rep.blocks("SUI")


def test_profitable_pair_not_blacklisted():
    trades = [_trade(symbol="BTC", pnl=5, close_type="TAKE_PROFIT", age_h=a)
              for a in (100, 80, 60, 40)]
    rep = evaluate_protections(trades, NOW, config=CFG)
    assert not rep.global_lock
    assert rep.blocks("BTC") is None


def test_future_trades_ignored():
    # replay evaluates at bar time: closes after now_ts must not leak in
    t = _trade(age_h=-2)  # 2h in the future
    rep = evaluate_protections([t], NOW, config=CFG)
    assert rep.blocks("BTC", "LONG") is None
