"""Unit tests for the v0.4 deterministic validation layer."""

from agent.validators import (
    GRID_DEFENSIVE,
    GRID_EXIT,
    GRID_NORMAL,
    GRID_WARNING,
    CostValidator,
    RiskValidator,
    compute_grid_features,
    evaluate_grid_state,
)

CFG = {
    "cost": {"maker_fee": 0.0002, "taker_fee": 0.0005,
             "slippage_pct": 0.0002, "safety_margin_pct": 0.0003},
    "risk": {"max_total_exposure_quote": 1500, "max_symbol_exposure_quote": 600,
             "max_drawdown_pct": 10},
    "grid_protection": {"warn_trend_rise": 1.25, "defensive_trend_strength": 0.004,
                        "warn_atr_rise": 1.3, "breakout_atr_mult": 0.5,
                        "news_severity_warn": 0.8},
}


def _validator() -> CostValidator:
    return CostValidator.from_config(CFG)


# --------------------------------------------------------------------- cost

def test_grid_below_fee_floor_rejected():
    """The bt-replay-7d failure mode: tp=0.02% cannot beat round-trip cost."""
    r = _validator().validate_grid(total_amount_quote=200, take_profit_pct=0.0002,
                                   max_open_orders=4)
    assert not r.approved
    assert r.net_profit <= 0
    assert r.reason == "EXPECTED_NET_PROFIT_NON_POSITIVE"


def test_grid_above_fee_floor_approved():
    r = _validator().validate_grid(total_amount_quote=200, take_profit_pct=0.002,
                                   max_open_orders=4)
    assert r.approved
    assert r.net_profit > 0
    # all cost components explicit, no hardcoded multiplier
    assert r.entry_fee > 0 and r.exit_fee > 0
    assert r.slippage_cost > 0 and r.safety_margin > 0


def test_position_cost_taker_both_sides():
    r = _validator().validate_position(notional_quote=200, take_profit_pct=0.02)
    assert r.approved
    assert abs(r.entry_fee - 200 * 0.0005) < 1e-9
    assert abs(r.exit_fee - 200 * 0.0005) < 1e-9


def test_position_thin_edge_rejected():
    r = _validator().validate_position(notional_quote=200, take_profit_pct=0.001)
    assert not r.approved  # 0.1% target < 0.1% taker fees + slippage + margin


# --------------------------------------------------------------------- risk

def test_risk_total_exposure_cap():
    rv = RiskValidator.from_config(CFG)
    r = rv.validate(symbol="BTC", new_exposure_quote=1000,
                    current_exposure_quote=600)
    assert not r.approved
    assert any("total exposure" in x for x in r.reasons)


def test_risk_symbol_exposure_cap():
    rv = RiskValidator.from_config(CFG)
    r = rv.validate(symbol="BTC", new_exposure_quote=200,
                    current_exposure_quote=200, symbol_exposure_quote=500)
    assert not r.approved
    assert any("BTC exposure" in x for x in r.reasons)


def test_risk_drawdown_blocks_new_exposure():
    rv = RiskValidator.from_config(CFG)
    r = rv.validate(symbol="BTC", new_exposure_quote=100,
                    current_exposure_quote=0, current_drawdown_pct=12.0)
    assert not r.approved
    assert any("drawdown" in x for x in r.reasons)


def test_risk_within_limits_approved():
    rv = RiskValidator.from_config(CFG)
    r = rv.validate(symbol="BTC", new_exposure_quote=200,
                    current_exposure_quote=200, symbol_exposure_quote=200,
                    current_drawdown_pct=3.0)
    assert r.approved


# --------------------------------------------------------- grid protection

def _ranging_candles(n: int = 120, base: float = 100.0) -> list[dict]:
    # flat oscillation inside a tight range
    return [
        {"high": base * 1.005 + (i % 4) * 0.1, "low": base * 0.995,
         "close": base + ((i % 6) - 3) * 0.05}
        for i in range(n)
    ]


def _trending_candles(n: int = 120) -> list[dict]:
    return [
        {"high": 100 + i * 0.4 + 0.2, "low": 100 + i * 0.4 - 0.2,
         "close": 100 + i * 0.4}
        for i in range(n)
    ]


def test_grid_state_normal_in_range():
    f = compute_grid_features(_ranging_candles())
    assert f  # features computed
    s = evaluate_grid_state(f, regime="RANGING", config=CFG)
    assert s.state in (GRID_NORMAL, GRID_WARNING)  # never blocked in a clean range


def test_grid_state_exit_on_breakout():
    candles = _ranging_candles()
    candles.append({"high": 103.0, "low": 102.0, "close": 102.8})
    f = compute_grid_features(candles)
    s = evaluate_grid_state(f, regime="RANGING", config=CFG)
    assert s.state == GRID_EXIT


def test_grid_state_defensive_on_trending_regime():
    f = compute_grid_features(_ranging_candles())
    s = evaluate_grid_state(f, regime="TRENDING_BULL", config=CFG)
    assert s.state == GRID_DEFENSIVE


def test_grid_state_defensive_on_regime_transition():
    f = compute_grid_features(_ranging_candles())
    s = evaluate_grid_state(f, regime="REGIME_TRANSITION", config=CFG)
    assert s.state == GRID_DEFENSIVE


def test_grid_state_exit_on_breakout_regime():
    s = evaluate_grid_state({}, regime="BREAKOUT", config=CFG)
    assert s.state == GRID_EXIT


def test_external_shock_escalates_to_warning():
    f = compute_grid_features(_ranging_candles())
    s = evaluate_grid_state(f, regime="RANGING",
                            external={"narrative_shock": True}, config=CFG)
    assert s.state in (GRID_WARNING, GRID_DEFENSIVE)
    assert "narrative shock detected" in s.reasons


def test_insufficient_data_is_not_exit():
    assert compute_grid_features(_ranging_candles(20)) == {}
    s = evaluate_grid_state({}, regime=None, config=CFG)
    assert s.state == GRID_NORMAL  # unknown features never fake an exit


def test_funding_cost_rejects_marginal_long():
    v = CostValidator(maker_fee=0.0002, taker_fee=0.0005,
                      slippage_pct=0.0002, safety_margin_pct=0.0003)
    # gross 0.25% clears fees+slippage+margin (0.15%) with room to spare
    r_no_funding = v.validate_position(1000.0, 0.0025)
    assert r_no_funding.approved
    assert r_no_funding.funding_cost == 0.0
    # one 8h settlement at +0.12% funding turns it negative
    r = v.validate_position(1000.0, 0.0025, funding_rate=0.0012,
                            expected_hold_hours=8.0, side="LONG")
    assert r.funding_cost == 1.2
    assert not r.approved
    assert "funding=1.2000" in r.summary()


def test_funding_income_never_credited():
    v = CostValidator(maker_fee=0.0002, taker_fee=0.0005,
                      slippage_pct=0.0002, safety_margin_pct=0.0003)
    # short while funding > 0 would RECEIVE funding — must not help approval
    r = v.validate_position(1000.0, 0.0012, funding_rate=0.001,
                            expected_hold_hours=24.0, side="SHORT")
    assert r.funding_cost == 0.0
    assert not r.approved  # still rejected on fees alone


def test_funding_charged_for_short_when_negative():
    v = CostValidator(maker_fee=0.0002, taker_fee=0.0005,
                      slippage_pct=0.0002, safety_margin_pct=0.0003)
    r = v.validate_position(1000.0, 0.01, funding_rate=-0.0004,
                            expected_hold_hours=16.0, side="SHORT")
    assert r.funding_cost == 0.8  # 2 settlements * 0.04% * 1000
    assert r.approved


def test_entry_gate_breakout_prefers_deseason_volume():
    from agent.validators import validate_entry
    base = {"ema_cross": "BULL", "ret_16bar_pct": 1.5, "rsi_14bar": 50}
    # raw z fails the threshold but the deseasonalized z confirms ->
    # the gate must use the deseasonalized one
    f = {**base, "volume_z_48bar": 0.3, "volume_z_deseason": 2.5}
    assert validate_entry(f, "LONG", regime="BREAKOUT").approved
    f2 = {**base, "volume_z_48bar": 3.0, "volume_z_deseason": 0.2}
    assert not validate_entry(f2, "LONG", regime="BREAKOUT").approved
    # fallback to raw z when deseason is unavailable
    f3 = {**base, "volume_z_48bar": 2.0}
    assert validate_entry(f3, "LONG", regime="BREAKOUT").approved
