"""Tests for ATR-scaled barriers and the breakeven stop in backtest_agent."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from backtest_agent import SimPosition, position_barriers, position_margin_for  # noqa: E402


class _Args:
    tp_pct = 0.02
    sl_pct = 0.01
    sl_atr_mult = 4.0
    rr = 2.5


def _pos(**kw) -> SimPosition:
    base = dict(symbol="BTC", side="LONG", entry=100.0, qty=1.0,
                deadline=1e12, opened_ts=0.0, executor_id="t",
                regime_at_entry=None, leverage=20.0)
    base.update(kw)
    return SimPosition(**base)


def test_barriers_scale_with_atr():
    # ATR 0.5% -> SL = 4x0.5% = 2%; horizon sigma = 0.5%*sqrt(48) = 3.46%
    # -> TP = min(2.5x2%, 0.9x3.46%) = 3.12% (horizon cap binds)
    tp, sl = position_barriers(0.5, _Args)
    assert abs(sl - 0.02) < 1e-9
    assert abs(tp - 0.9 * 0.005 * math.sqrt(48)) < 1e-9


def test_barriers_floor_and_cap():
    # tiny ATR -> SL floor at sl_pct; TP floor keeps rr >= 1.2
    tp, sl = position_barriers(0.1, _Args)
    assert sl == _Args.sl_pct and abs(tp - 1.2 * _Args.sl_pct) < 1e-9
    # huge ATR -> SL cap at 3x sl_pct, TP = rr x SL (horizon cap far away)
    tp, sl = position_barriers(5.0, _Args)
    assert sl == 3 * _Args.sl_pct and abs(tp - _Args.rr * 3 * _Args.sl_pct) < 1e-9


def test_tp_never_exceeds_horizon_sigma():
    # whatever the rr, TP stays within 0.9x the holding horizon's 1-sigma
    for atr in (0.2, 0.35, 0.5, 0.8, 1.2):
        tp, sl = position_barriers(atr, _Args)
        assert tp <= 0.9 * atr / 100 * math.sqrt(48) + 1e-12 or tp == 1.2 * sl
        assert tp >= 1.2 * sl - 1e-12


def test_barriers_disabled_without_atr():
    _Args.sl_atr_mult = 0.0
    try:
        tp, sl = position_barriers(0.5, _Args)
        assert (tp, sl) == (_Args.tp_pct, _Args.sl_pct)
    finally:
        _Args.sl_atr_mult = 4.0


def test_breakeven_moves_stop_to_entry():
    p = _pos(sl_pct=0.01, tp_pct=0.025, breakeven=True)
    # candle reaches +1.5% (past the +1% trigger) without touching the stop
    assert p.check_exit({"high": 101.5, "low": 99.6, "close": 101.2}, 1) is None
    assert p.be_active
    # retrace to entry -> scratch exit, not a full stop loss
    out = p.check_exit({"high": 100.8, "low": 99.95, "close": 100.0}, 2)
    assert out is not None and out[1] == "BREAKEVEN_EXIT"
    # only fees are lost at entry price
    assert abs(out[0] + out[2]) < 1e-9


def test_same_candle_trigger_and_stop_resolves_conservatively():
    p = _pos(sl_pct=0.01, tp_pct=0.025, breakeven=True)
    # one candle touches both trigger (high 101.5) and stop (low 98.9):
    # assume the stop hit first — no breakeven rescue
    out = p.check_exit({"high": 101.5, "low": 98.9, "close": 99.0}, 1)
    assert out is not None and out[1] == "STOP_LOSS"
    assert not p.be_active


def test_breakeven_off_keeps_fixed_stop():
    p = _pos(sl_pct=0.01, tp_pct=0.025, breakeven=False)
    p.check_exit({"high": 101.5, "low": 99.6, "close": 101.2}, 1)
    assert not p.be_active
    out = p.check_exit({"high": 100.8, "low": 99.0, "close": 99.1}, 2)
    assert out is not None and out[1] == "STOP_LOSS"


def test_short_breakeven_symmetric():
    p = _pos(side="SHORT", sl_pct=0.01, tp_pct=0.025, breakeven=True)
    assert p.check_exit({"high": 100.4, "low": 98.5, "close": 98.8}, 1) is None
    assert p.be_active
    out = p.check_exit({"high": 100.05, "low": 99.2, "close": 100.0}, 2)
    assert out is not None and out[1] == "BREAKEVEN_EXIT"


def test_trailing_locks_in_profit():
    p = _pos(sl_pct=0.01, tp_pct=0.10, breakeven=True, trailing=True)
    # +1.5%: activates breakeven and seeds the trail at the candle high
    assert p.check_exit({"high": 101.5, "low": 99.6, "close": 101.4}, 1) is None
    assert p.be_active
    # +4%: trail extends; stop should now sit at 103.0 (104 - 1% of entry)
    assert p.check_exit({"high": 104.0, "low": 100.9, "close": 103.8}, 2) is None
    # retrace to 102.9 low -> stopped at 103.0, locking +3% gross
    out = p.check_exit({"high": 103.4, "low": 102.9, "close": 103.0}, 3)
    assert out is not None and out[1] == "TRAIL_EXIT"
    assert out[0] > 0  # profitable despite being "stopped out"


def test_trailing_never_below_entry():
    p = _pos(sl_pct=0.01, tp_pct=0.10, breakeven=True, trailing=True)
    p.check_exit({"high": 101.5, "low": 99.6, "close": 101.2}, 1)
    # trails 1x SL (1.0) behind the activation candle's high (101.5),
    # and never below entry (100.0)
    assert p._stop_price() == 100.5
    assert p._stop_price() >= p.entry


def test_position_margin_for_vol_targeting():
    class A:
        risk_per_trade_pct = 0.02
        leverage = 5.0
        position_margin = 2000.0
    # equity 10k, SL 1%: margin = 200 / (5 x 0.01) = 4000 -> capped at 2000
    assert position_margin_for(10_000, 0.01, A) == 2000.0
    # SL 2%: margin = 200 / (5 x 0.02) = 2000
    assert position_margin_for(10_000, 0.02, A) == 2000.0
    # SL 4%: margin = 200 / (5 x 0.04) = 1000 — wider stop, smaller size
    assert position_margin_for(10_000, 0.04, A) == 1000.0
    # disabled -> fixed margin
    A.risk_per_trade_pct = 0.0
    assert position_margin_for(10_000, 0.02, A) == 2000.0
