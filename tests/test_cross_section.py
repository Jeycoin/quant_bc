"""Unit tests for the deterministic cross-sectional ranking layer (v0.6 L1)."""

from intelligence.cross_section import score_symbols


def _feat(mom8=0.0, mom24=0.0, ema=0.0, volz=0.0, ema_cross="BULL",
          funding=0.0):
    return {"ret_16bar_pct": mom8, "ret_48bar_pct": mom24,
            "ema_dist_pct": ema, "volume_z_deseason": volz,
            "ema_cross": ema_cross, "funding_rate": funding}


def test_ranking_prefers_strongest_momentum():
    feats = {
        "BTC": _feat(mom8=1.0, mom24=2.0, ema=0.5),
        "ETH": _feat(mom8=3.0, mom24=5.0, ema=1.0, volz=2.0),
        "SOL": _feat(mom8=-2.0, mom24=-4.0, ema=-1.0, ema_cross="BEAR"),
    }
    xs = score_symbols(feats, top_n=2, min_dispersion=0.1)
    assert xs["ranking"][0] == "ETH"
    assert xs["ranking"][-1] == "SOL"
    assert "ETH" in xs["long_candidates"]
    assert "SOL" not in xs["long_candidates"]


def test_dispersion_gate_blocks_uniform_market():
    # all five identical -> no cross-sectional information -> no candidates
    feats = {s: _feat(mom8=1.0, mom24=1.0, ema=0.5) for s in
             ("BTC", "ETH", "SOL", "XRP", "SUI")}
    xs = score_symbols(feats, min_dispersion=0.5)
    assert xs["dispersion"] == 0.0
    assert xs["long_candidates"] == []
    assert xs["short_candidates"] == []


def test_short_candidate_requires_bottom_rank_and_downtrend():
    feats = {
        "BTC": _feat(mom8=2.0, ema=0.5),
        "ETH": _feat(mom8=1.0, ema=0.3),
        "SOL": _feat(mom8=-3.0, mom24=-5.0, ema=-1.0, ema_cross="BEAR"),
    }
    xs = score_symbols(feats, min_dispersion=0.1)
    assert xs["short_candidates"] == ["SOL"]
    # same weak score but NOT a higher-timeframe downtrend -> no short
    feats["SOL"]["ema_cross"] = "BULL"
    xs = score_symbols(feats, min_dispersion=0.1)
    assert xs["short_candidates"] == []
    feats["SOL"]["ema_cross"] = "BEAR"
    feats["SOL"]["ret_16bar_pct"] = 0.5  # weak score but 8h momentum up
    xs = score_symbols(feats, min_dispersion=0.1)
    assert xs["short_candidates"] == []


def test_funding_crowding_penalizes_outlier():
    base = dict(mom8=1.0, mom24=1.0, ema=0.5)
    feats = {s: _feat(**base) for s in ("BTC", "ETH", "SOL")}
    xs_clean = score_symbols(feats, min_dispersion=0.0)
    feats["SOL"]["funding_rate"] = 0.01  # 1%/8h — extremely crowded long
    xs = score_symbols(feats, min_dispersion=0.0)
    assert xs["scores"]["SOL"] < xs_clean["scores"]["SOL"]
    # others are unaffected (funding penalty only marks the outlier down)
    assert xs["scores"]["BTC"] == xs_clean["scores"]["BTC"]


def test_rotation_requires_btc_absolute_strength():
    # alts relatively stronger while BTC falls -> NOT alt favor (the
    # "BTC fell faster" trap, frontier notes §10)
    feats = {
        "BTC": _feat(mom8=-1.0, ema=-0.2, ema_cross="BEAR"),
        "ETH": _feat(mom8=0.5, ema=0.2),
        "SOL": _feat(mom8=0.8, ema=0.3),
        "XRP": _feat(mom8=0.6, ema=0.2),
    }
    xs = score_symbols(feats, min_dispersion=0.1)
    assert xs["rotation"] != "ALT_FAVOR"
    # alts stronger AND BTC rising -> genuine rotation
    feats["BTC"] = _feat(mom8=0.5, ema=0.1)
    xs = score_symbols(feats, min_dispersion=0.1)
    assert xs["rotation"] == "ALT_FAVOR"


def test_volume_signed_by_momentum_direction():
    # abnormal volume against the 8h momentum must not help the score
    feats = {
        "BTC": _feat(mom8=1.0, ema=0.3, volz=3.0),   # volume WITH momentum
        "ETH": _feat(mom8=1.0, ema=0.3, volz=0.0),
        "SOL": _feat(mom8=-1.0, ema=-0.3, ema_cross="BEAR", volz=3.0),
    }
    xs = score_symbols(feats, min_dispersion=0.0)
    assert xs["scores"]["BTC"] > xs["scores"]["ETH"]
