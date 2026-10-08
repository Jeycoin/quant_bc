"""Tests for the framework v0.5 entry gate (agent.validators.validate_entry)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.validators import validate_entry  # noqa: E402

CFG = {"entry_gate": {"enabled": True, "rsi_overbought": 75,
                      "rsi_oversold": 25, "breakout_volume_z": 1.0}}

BULL_FEATURES = {"ema_cross": "BULL", "ret_16bar_pct": 1.2,
                 "rsi_14bar": 58.0, "volume_z_48bar": 1.5}
BEAR_FEATURES = {"ema_cross": "BEAR", "ret_16bar_pct": -1.4,
                 "rsi_14bar": 41.0, "volume_z_48bar": 1.8}


def test_aligned_long_passes():
    r = validate_entry(BULL_FEATURES, "LONG", "TRENDING_BULL", CFG)
    assert r.approved


def test_aligned_short_passes():
    r = validate_entry(BEAR_FEATURES, "SHORT", "TRENDING_BEAR", CFG)
    assert r.approved


def test_counter_trend_blocked():
    # the classic agent failure: longing into a bear cross / negative momentum
    r = validate_entry(BEAR_FEATURES, "LONG", "TRENDING_BEAR", CFG)
    assert not r.approved
    assert any("counter-regime" in x for x in r.reasons)
    assert any("counter-trend" in x for x in r.reasons)
    assert any("momentum" in x for x in r.reasons)


def test_ranging_blocks_directional():
    r = validate_entry(BULL_FEATURES, "LONG", "RANGING", CFG)
    assert not r.approved
    assert any("RANGING" in x for x in r.reasons)


def test_rsi_exhaustion_guard():
    f = dict(BULL_FEATURES, rsi_14bar=78.0)
    assert not validate_entry(f, "LONG", "TRENDING_BULL", CFG).approved
    f = dict(BEAR_FEATURES, rsi_14bar=22.0)
    assert not validate_entry(f, "SHORT", "TRENDING_BEAR", CFG).approved


def test_breakout_needs_volume():
    quiet = dict(BULL_FEATURES, volume_z_48bar=0.3)
    r = validate_entry(quiet, "LONG", "BREAKOUT", CFG)
    assert not r.approved
    assert any("volume" in x for x in r.reasons)
    loud = dict(BULL_FEATURES, volume_z_48bar=2.4)
    assert validate_entry(loud, "LONG", "BREAKOUT", CFG).approved


def test_regime_optional():
    # live path has no LLM regime at validation time — objective rules only
    r = validate_entry(BULL_FEATURES, "LONG", None, CFG)
    assert r.approved


def test_gate_disabled():
    r = validate_entry(BEAR_FEATURES, "LONG", "TRENDING_BEAR",
                       {"entry_gate": {"enabled": False}})
    assert r.approved


def test_non_directional_actions_pass():
    assert validate_entry(BEAR_FEATURES, "WAIT", "RANGING", CFG).approved
    assert validate_entry(None, "WATCH", None, CFG).approved
