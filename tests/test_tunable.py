"""Tests for agent/tunable.py — bounded AI control over gate dials."""

import copy

import yaml

from agent import tunable as t
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _config():
    with open(REPO / "config" / "settings.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _mini_config():
    return {
        "entry_gate": {"rsi_overbought": 75},
        "tunable": {
            "entry_gate.rsi_overbought": {"default": 75, "min": 65, "max": 85},
            "time_stop_hours": {"default": 6, "min": 2, "max": 24},
            "protections.stoploss_guard.trade_limit":
                {"default": 4, "min": 2, "max": 8},
            "leverage": {"default": 1, "min": 1, "max": 20},
            "position_notional_quote": {"default": 200, "min": 50, "max": 500},
            "position_take_profit_pct":
                {"default": 0.02, "min": 0.005, "max": 0.05},
        },
    }


# --------------------------------------------------------------- tunable_spec

def test_spec_loads_from_real_settings():
    spec = t.tunable_spec(_config())
    # every entry must carry a coherent envelope
    for path, s in spec.items():
        assert s["min"] <= s["default"] <= s["max"], path
    # hard safety boundaries must NOT be tunable
    forbidden = [p for p in spec if p.startswith("safety")
                 or p.startswith("risk.max_")
                 or "LIVE" in p.upper()]
    assert forbidden == []
    assert "entry_gate.rsi_overbought" in spec


def test_spec_handles_missing_section():
    assert t.tunable_spec({}) == {}
    assert t.tunable_spec(None) == {}
    assert t.tunable_spec({"tunable": {"bad": "not-a-dict",
                                       "bad2": {"default": "x"}}}) == {}


# ------------------------------------------------------- resolve_adjustments

def test_valid_adjustment_applied():
    r = t.resolve_adjustments(
        {"entry_gate.rsi_overbought": {"value": 78, "reason": "strong trend"}},
        _mini_config())
    assert r["overrides"] == {"entry_gate.rsi_overbought": 78.0}
    assert r["events"][0]["clamped"] is False
    assert r["events"][0]["reason"] == "strong trend"
    assert r["rejected"] == []


def test_out_of_range_is_clamped_and_logged():
    r = t.resolve_adjustments(
        {"entry_gate.rsi_overbought": {"value": 95, "reason": "too greedy"},
         "time_stop_hours": {"value": 1, "reason": "below floor"}},
        _mini_config())
    assert r["overrides"]["entry_gate.rsi_overbought"] == 85.0   # clamped to max
    assert r["overrides"]["time_stop_hours"] == 2                # clamped to min
    assert all(e["clamped"] for e in r["events"])
    assert r["rejected"] == []


def test_unknown_path_rejected():
    r = t.resolve_adjustments(
        {"risk.max_total_exposure_quote": {"value": 99999, "reason": "yolo"},
         "safety.max_order_quote_amount": {"value": 1e6, "reason": "yolo"}},
        _mini_config())
    assert r["overrides"] == {}
    assert len(r["rejected"]) == 2
    assert all("unknown_path" in x["error"] for x in r["rejected"])


def test_bad_type_rejected():
    for bad in ("high", [80], {"x": 1}, True, None, float("nan")):
        r = t.resolve_adjustments(
            {"entry_gate.rsi_overbought": {"value": bad, "reason": "test"}},
            _mini_config())
        assert r["overrides"] == {}, bad
        assert r["rejected"] and "bad_type" in r["rejected"][0]["error"], bad


def test_reason_required():
    for missing in ({"value": 80}, {"value": 80, "reason": ""},
                    {"value": 80, "reason": "   "}):
        r = t.resolve_adjustments(
            {"entry_gate.rsi_overbought": missing}, _mini_config())
        assert r["overrides"] == {}, missing
        assert "reason_required" in r["rejected"][0]["error"]


def test_malformed_payloads_rejected_not_raised():
    for bad in ("not-a-dict", [1, 2], 42, {"x": "not-a-spec"}):
        r = t.resolve_adjustments(bad, _mini_config())
        assert r["overrides"] == {}
        assert r["rejected"]


def test_integral_knob_stays_int():
    r = t.resolve_adjustments(
        {"protections.stoploss_guard.trade_limit":
         {"value": 6, "reason": "more patience"}},
        _mini_config())
    assert r["overrides"]["protections.stoploss_guard.trade_limit"] == 6
    assert isinstance(
        r["overrides"]["protections.stoploss_guard.trade_limit"], int)


# --------------------------------------------------------- effective_config

def test_effective_config_dotted_and_flat():
    cfg = _mini_config()
    eff = t.effective_config(cfg, {"entry_gate.rsi_overbought": 80,
                                   "time_stop_hours": 12})
    assert eff["entry_gate"]["rsi_overbought"] == 80
    assert eff["time_stop_hours"] == 12


def test_effective_config_never_mutates_input():
    cfg = _mini_config()
    snapshot = copy.deepcopy(cfg)
    t.effective_config(cfg, {"entry_gate.rsi_overbought": 80})
    assert cfg == snapshot  # per-decision scope enforced by construction


# ------------------------------------------------------ clamp_executor_knobs

def test_knob_clamp_leverage_and_barriers():
    cfg = {"leverage": 50,
           "triple_barrier_config": {"take_profit": 0.10, "stop_loss": 0.01}}
    events = t.clamp_executor_knobs(cfg, "position_executor", _mini_config())
    assert cfg["leverage"] == 20
    assert cfg["triple_barrier_config"]["take_profit"] == 0.05
    assert cfg["triple_barrier_config"]["stop_loss"] == 0.01  # inside bounds
    assert len(events) == 2
    assert all(e["clamped"] for e in events)


def test_knob_clamp_grid_uses_grid_bounds():
    cfg = {"triple_barrier_config": {"take_profit": 0.0001}}
    events = t.clamp_executor_knobs(cfg, "grid_executor",
                                    {"tunable": {"grid_take_profit_pct":
                                                 {"default": 0.002,
                                                  "min": 0.001, "max": 0.05}}})
    assert cfg["triple_barrier_config"]["take_profit"] == 0.001
    assert len(events) == 1


def test_knob_clamp_quote_notional():
    cfg = {"total_amount_quote": 5000}
    events = t.clamp_executor_knobs(cfg, "grid_executor", _mini_config())
    assert cfg["total_amount_quote"] == 500
    assert len(events) == 1


def test_knob_clamp_noop_inside_bounds():
    cfg = {"leverage": 5, "total_amount_quote": 300,
           "triple_barrier_config": {"take_profit": 0.02}}
    assert t.clamp_executor_knobs(cfg, "position_executor",
                                  _mini_config()) == []
    assert cfg["leverage"] == 5


def test_knob_clamp_amount_with_price():
    cfg = {"amount": 1.0}  # 1 BTC * 80000 = way over the 500 cap
    events = t.clamp_executor_knobs(cfg, "position_executor",
                                    _mini_config(), price=80000.0)
    assert cfg["amount"] == 500 / 80000.0
    assert len(events) == 1


def test_knob_clamp_tolerates_garbage():
    assert t.clamp_executor_knobs(None, "grid_executor", _mini_config()) == []
    assert t.clamp_executor_knobs({"leverage": "high"}, "grid_executor",
                                  _mini_config()) == []
