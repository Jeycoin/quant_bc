"""Agent replay backtest: forward-replay the LLM over historical data.

The open question of this project is whether the AGENT adds value over a
fixed strategy. That cannot be answered by deterministic backtesting — so
we replay history: at each simulated decision point the LLM sees only data
available at that time (candles, funding, simulated portfolio), decides,
and its decisions execute against subsequent candles.

Contamination caveat: the model's training cutoff is unknown; recent-window
replay reduces but cannot eliminate look-ahead. Results are indicative —
they calibrate expectations, they do not prove live performance.

Simplifications (all documented in the report):
- no order book history (not available) — candles + funding only
- flat 0.04% fee per side, candle-range fills, no slippage
- deterministic execution policy: grid when strategy=grid in RANGING,
  directional position when action=LONG/SHORT with confidence >= threshold
- directional barriers are ATR-scaled by default (SL = 4x ATR14%, clamped to
  [sl_pct, 3x sl_pct]; TP = min(2.5x SL, 0.9x horizon sigma)) with a
  breakeven stop that moves to entry after +1x SL favorable excursion and a
  trailing stop 1x SL behind the extreme thereafter
- framework v0.5 adds: a deterministic entry gate (8h momentum + EMA cross
  + RSI guard + breakout volume confirmation; no directional trades in
  RANGING), volatility-targeted sizing (2% equity risk per trade), and a
  6h same-direction cooldown after stop-outs — see
  docs/research/crypto-quant-frontier-notes.md

Usage:
  python scripts/backtest_agent.py [--days 7] [--bar-minutes 30]
      [--decision-minutes 30] [--resume-exp EXP_ID]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

load_dotenv(REPO_ROOT / ".env")

from agent.agent import extract_analysis_blocks, normalize_regime
from agent.market_features import compute_market_features
from agent.validators import (
    GRID_DEFENSIVE,
    GRID_EXIT,
    GRID_WARNING,
    CostValidator,
    RiskValidator,
    compute_grid_features,
    evaluate_grid_state,
    validate_entry,
)
from analytics.store import AnalyticsStore, new_decision_id
from analytics.versioning import current_versions
from backtest_grid import FEE_RATE, GridSim, fetch_candles
from integrations.llm import create_llm_client
from intelligence.cross_section import score_symbols

SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "SUI"]
POSITION_SIZE = 200.0
GRID_SIZE = 200.0
GRID_TP = 0.002          # informed by the fee-floor backtest
GRID_RANGE_PCT = 0.025
POS_TP_PCT = 0.02
POS_SL_PCT = 0.01
POS_TIME_LIMIT_S = 86400
MIN_CONFIDENCE = 0.6

REPLAY_ADDENDUM = """

# Historical replay mode

You are in a historical market replay. Current time is given in the user
message. All market data is provided inline — no tools are available, do
not attempt tool calls. Use ONLY the provided data; do not use any outside
knowledge of what happened after the stated time. The `markets` block
contains multi-horizon features computed on fresh short-term bars: ret_Nbar
returns, range positions, realized volatility, volume z-score, EMA cross,
RSI and ATR (1 bar = bar_minutes, so 8 bars = 4h at 30m bars); the 48-bar
(24h) fields and funding_rate are slow background context. Simulated
execution policy: strategy "grid" in a ranging regime opens a grid;
LONG/SHORT at or above the configured confidence threshold opens a
LEVERAGED directional position — but only if it passes the deterministic
entry gate: direction must agree with 8h momentum (ret_16bar) and the
EMA(8/21) cross, RSI extremes block chase entries, BREAKOUT needs volume
confirmation (|volume_z| >= 1), and directional trades are disabled in
RANGING (grids are for ranges). Positions use ATR-scaled TP/SL, a
breakeven stop, a 1x-SL trailing stop, and volatility-targeted sizing
(margin = 2% of equity risked per trade); liquidation is simulated —
high leverage can wipe out the margin on a modest adverse move. After a
stop-out, the same direction on that symbol cools down for 6h. WAIT/WATCH
does nothing. LONG and SHORT are fully symmetric in this replay — same
costs, same barriers, both always available. Keep your confidence score
honest — inflated confidence degrades the experiment. End your reply with
one ```analysis block per symbol.
"""


@dataclass
class SimPosition:
    symbol: str
    side: str  # LONG | SHORT
    entry: float
    qty: float
    deadline: float
    opened_ts: float
    executor_id: str
    regime_at_entry: str | None
    margin: float = POSITION_SIZE       # quote currency posted as margin
    leverage: float = 1.0
    tp_pct: float = POS_TP_PCT
    sl_pct: float = POS_SL_PCT
    breakeven: bool = False   # move SL to entry after +1x SL favorable excursion
    trailing: bool = False    # after breakeven, trail 1x SL behind the extreme
    time_stop_s: float = 0.0  # exit at market if be trigger not hit in time
    be_active: bool = False
    trail_best: float = 0.0   # best favorable price seen since activation

    def _stop_price(self) -> float:
        """Current effective stop. Plain SL before activation; entry after
        breakeven; trailed 1x sl_pct behind the favorable extreme when
        trailing is on (never worse than entry once active)."""
        if not self.be_active:
            return self.entry * (1 - self.sl_pct) if self.side == "LONG" \
                else self.entry * (1 + self.sl_pct)
        if not self.trailing or not self.trail_best:
            return float(self.entry)
        dist = self.sl_pct * self.entry
        if self.side == "LONG":
            return max(float(self.entry), self.trail_best - dist)
        return min(float(self.entry), self.trail_best + dist)

    def check_exit(self, c: dict, ts: float) -> tuple[float, str, float] | None:
        """Returns (net_pnl, reason, total_fees) or None.

        Includes liquidation: with leverage L the position is wiped out when
        price moves ~1/L against it (isolated-margin approximation — the
        exchange's maintenance margin would liquidate slightly earlier).
        Breakeven: once price has moved +1x sl_pct in favor without touching
        the original stop, the stop moves to entry (a scratch exit still
        pays fees). Trailing (QuantPedia D1H1 lesson: trailing exits beat
        fixed TP in trend regimes): once active, the stop trails 1x sl_pct
        behind the best favorable price. Intra-candle ambiguity resolves
        conservatively: if a single candle touches both the trigger and the
        stop, the stop is assumed hit first; the trailing extreme updates
        only on candles where no exit fired.
        """
        high, low, close = float(c["high"]), float(c["low"]), float(c["close"])
        sl = self._stop_price()
        tp = self.entry * (1 + self.tp_pct) if self.side == "LONG" else self.entry * (1 - self.tp_pct)
        liq = self.entry * (1 - 1 / self.leverage) if self.side == "LONG" \
            else self.entry * (1 + 1 / self.leverage)
        sign = 1 if self.side == "LONG" else -1
        exit_price = None
        reason = ""
        if self.side == "LONG" and low <= liq and liq > sl:
            exit_price, reason = liq, "LIQUIDATION"
        elif self.side == "SHORT" and high >= liq and liq < sl:
            exit_price, reason = liq, "LIQUIDATION"
        elif self.side == "LONG" and low <= sl:
            exit_price = sl
            reason = "STOP_LOSS" if not self.be_active else (
                "TRAIL_EXIT" if self.trailing and sl > self.entry else "BREAKEVEN_EXIT")
        elif self.side == "SHORT" and high >= sl:
            exit_price = sl
            reason = "STOP_LOSS" if not self.be_active else (
                "TRAIL_EXIT" if self.trailing and sl < self.entry else "BREAKEVEN_EXIT")
        elif self.side == "LONG" and high >= tp:
            exit_price, reason = tp, "TAKE_PROFIT"
        elif self.side == "SHORT" and low <= tp:
            exit_price, reason = tp, "TAKE_PROFIT"
        elif self.time_stop_s and not self.be_active \
                and ts - self.opened_ts >= self.time_stop_s:
            # short-term time stop: a trade that has not reached +1x SL
            # within the window never had momentum behind it — scratch it
            # at market instead of donating fees/attention to a dead position
            exit_price, reason = close, "TIME_STOP"
        elif ts >= self.deadline:
            exit_price, reason = close, "TIME_LIMIT"
        if exit_price is None and (self.breakeven or self.trailing):
            trigger = self.entry * (1 + self.sl_pct) if self.side == "LONG" \
                else self.entry * (1 - self.sl_pct)
            orig_sl_hit = (low <= self.entry * (1 - self.sl_pct)) if self.side == "LONG" \
                else (high >= self.entry * (1 + self.sl_pct))
            trig_hit = (high >= trigger) if self.side == "LONG" else (low <= trigger)
            if not self.be_active:
                if trig_hit and not orig_sl_hit:
                    self.be_active = True
                    self.trail_best = high if self.side == "LONG" else low
            elif self.trailing:
                if self.side == "LONG":
                    self.trail_best = max(self.trail_best, high)
                else:
                    self.trail_best = min(self.trail_best, low)
        if exit_price is None:
            return None
        gross = (exit_price - self.entry) * self.qty * sign
        fees = (self.entry + exit_price) * self.qty * FEE_RATE
        return gross - fees, reason, fees


@dataclass
class SimGrid:
    sim: GridSim
    symbol: str
    opened_ts: float
    executor_id: str
    regime_at_entry: str | None


def _resample(candles: list[dict], factor: int) -> list[dict]:
    """Aggregate `factor` consecutive bars into one. Used to keep the grid
    protection state machine on its original 1h semantics while the LLM
    features run on shorter bars."""
    if factor <= 1:
        return candles
    out = []
    for i in range(0, len(candles) - factor + 1, factor):
        grp = candles[i:i + factor]
        out.append({
            "timestamp": grp[0]["timestamp"], "open": grp[0]["open"],
            "high": max(g["high"] for g in grp),
            "low": min(g["low"] for g in grp),
            "close": grp[-1]["close"],
            "volume": sum(g["volume"] for g in grp),
        })
    return out


def position_barriers(atr_pct: float | None, args,
                      bar_seconds: int = 1800,
                      horizon_s: float = POS_TIME_LIMIT_S) -> tuple[float, float]:
    """ATR-scaled (tp_pct, sl_pct) for directional positions.

    Lesson from bt-replay-7d-aggressive (exp-66c504e488ed): a fixed 1% SL
    against multi-hour holding periods sits inside the noise band — 10 stops
    vs 4 take-profits. Lesson from exp-5a5bc85adf5b: TP = 2.5x SL can exceed
    the whole holding horizon's 1-sigma move and never triggers (0 TP exits,
    winners capped by TIME_LIMIT while losers pay full SL). So both barriers
    must be consistent with the holding horizon:

      SL = clamp(sl_atr_mult x ATR14%%, sl_pct floor, 3x sl_pct cap)
      TP = min(rr x SL, 0.9 x sigma_horizon), at least 1.2x SL

    where sigma_horizon = ATR%% x sqrt(horizon_bars), capped at 48 bars
    (price series are closer to trending than pure random walk, so the cap
    keeps the estimate conservative).
    """
    if args.sl_atr_mult > 0 and atr_pct:
        atr = atr_pct / 100.0
        sl = min(max(args.sl_atr_mult * atr, args.sl_pct), 3 * args.sl_pct)
        horizon_bars = max(1.0, min(horizon_s / bar_seconds, 48))
        sigma_h = atr * math.sqrt(horizon_bars)
        tp = min(args.rr * sl, 0.9 * sigma_h)
        tp = max(tp, 1.2 * sl)
        return tp, sl
    return args.tp_pct, args.sl_pct


def position_margin_for(equity: float, sl_pct: float, args) -> float:
    """Volatility-targeted margin (framework v0.5): risk a fixed fraction of
    equity per trade — margin x leverage x SL% = equity x risk_pct. Wider
    (high-vol) stops automatically shrink the position. Capped at
    --position-margin, floored at 50 so the sim stays meaningful."""
    if args.risk_per_trade_pct > 0 and sl_pct > 0 and args.leverage > 0:
        m = equity * args.risk_per_trade_pct / (args.leverage * sl_pct)
        return min(max(m, 50.0), args.position_margin)
    return args.position_margin


async def fetch_funding(coin: str, start_ms: int) -> dict[float, float]:
    # trust_env=False: see fetch_candles in backtest_grid.py
    async with httpx.AsyncClient(base_url="https://api.hyperliquid.xyz",
                                 timeout=30.0, trust_env=False,
                                 proxy=os.environ.get("MARKET_DATA_PROXY") or None) as client:
        resp = await client.post("/info", json={
            "type": "fundingHistory", "coin": coin, "startTime": start_ms,
        })
        resp.raise_for_status()
        return {f["time"] / 1000: float(f["fundingRate"]) for f in resp.json()}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Agent replay backtest")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--bar-minutes", type=int, default=30,
                        help="candle bar size; LLM features are computed on these bars")
    parser.add_argument("--decision-minutes", type=int, default=30,
                        help="decision cadence; should match the live loop interval")
    parser.add_argument("--resume-exp", default=None,
                        help="resume an interrupted replay experiment: rebuild "
                             "portfolio state from its recorded decisions without "
                             "LLM calls, then continue live decisions up to now")
    # risk-appetite knobs (replay only; live limits live in config/settings.yaml)
    parser.add_argument("--leverage", type=float, default=1.0,
                        help="position leverage; qty = margin * leverage / price")
    parser.add_argument("--position-margin", type=float, default=POSITION_SIZE,
                        help="margin (quote) posted per directional position")
    parser.add_argument("--tp-pct", type=float, default=POS_TP_PCT)
    parser.add_argument("--sl-pct", type=float, default=POS_SL_PCT,
                        help="also the floor for ATR-scaled stops")
    parser.add_argument("--sl-atr-mult", type=float, default=4.0,
                        help="SL = clamp(sl_atr_mult x ATR14%%, sl_pct, 3x sl_pct); "
                             "0 disables ATR scaling (fixed --sl-pct/--tp-pct)")
    parser.add_argument("--rr", type=float, default=2.5,
                        help="reward:risk ratio; TP = rr x SL when ATR scaling active")
    parser.add_argument("--no-breakeven", action="store_true",
                        help="disable breakeven stop (default: SL moves to entry "
                             "after +1x SL favorable excursion)")
    parser.add_argument("--no-trailing", action="store_true",
                        help="disable trailing stop (default: once at breakeven, "
                             "the stop trails 1x SL behind the favorable extreme)")
    parser.add_argument("--risk-per-trade-pct", type=float, default=0.02,
                        help="volatility-targeted sizing: margin = equity x this "
                             "/ (leverage x SL%%), capped by --position-margin; "
                             "0 disables (fixed margin)")
    parser.add_argument("--cooldown-hours", type=float, default=6.0,
                        help="after a STOP_LOSS/LIQUIDATION, block same-symbol "
                             "same-side entries for this many hours; 0 disables")
    parser.add_argument("--time-stop-hours", type=float, default=6.0,
                        help="short-term time stop: exit at market when a "
                             "position has been open this long WITHOUT reaching "
                             "its breakeven trigger (a trade that has not worked "
                             "within a few hours is noise, not a thesis); "
                             "0 disables, the 24h TIME_LIMIT stays as backstop")
    parser.add_argument("--min-confidence", type=float, default=MIN_CONFIDENCE)
    parser.add_argument("--no-cross-section", dest="cross_section",
                        action="store_false",
                        help="disable the v0.6 cross-sectional ranking gate "
                             "(control group for experiments)")
    parser.set_defaults(cross_section=True)
    parser.add_argument("--top-n", type=int, default=2,
                        help="cross-section: max long candidates per cycle")
    parser.add_argument("--min-dispersion", type=float, default=0.5,
                        help="cross-section: below this score dispersion the "
                             "market moves as one and no directional entries "
                             "are allowed at all")
    parser.add_argument("--same-dir-discount", type=float, default=0.5,
                        help="portfolio layer: risk-budget multiplier per "
                             "additional same-direction position")
    parser.add_argument("--grid-size", type=float, default=GRID_SIZE)
    parser.add_argument("--max-total-exposure", type=float, default=None,
                        help="override risk.max_total_exposure_quote for this run")
    parser.add_argument("--max-symbol-exposure", type=float, default=None,
                        help="override risk.max_symbol_exposure_quote for this run")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    end_ts = int(time.time())
    start_ts = end_ts - args.days * 86400

    store = AnalyticsStore(str(REPO_ROOT / "data" / "analytics.db"))
    recorded_blocks: dict[float, list[dict]] = {}
    recorded_by_ts_sym: dict[tuple[float, str], dict] = {}
    recorded_closes: dict[float, list[dict]] = {}
    resume_last_ts: float | None = None
    exp_id: str | None = None
    if args.resume_exp:
        exp = store.get_experiment(args.resume_exp)
        if not exp:
            raise SystemExit(f"experiment {args.resume_exp} not found")
        exp_id = args.resume_exp
        events = store.list_decision_events(exp_id)
        trades = store.list_trade_events(exp_id)
        if not events:
            raise SystemExit(f"experiment {exp_id} has no recorded decisions")
        block_events = [e for e in events if e.get("symbol")]
        if not block_events:
            raise SystemExit(f"experiment {exp_id} has no recorded block decisions")
        # NO_BLOCK rows carry wall-clock ts, not candle ts — only block events
        # give reliable candle-aligned timestamps for the resume boundary
        resume_last_ts = max(float(e["ts"]) for e in block_events)
        start_ts = int(min(float(e["ts"]) for e in block_events))
        summary = json.loads(exp["summary_json"]) if exp.get("summary_json") else {}
        for e in events:
            if e.get("symbol"):
                blk = {"symbol": e["symbol"], "regime": e.get("market_regime"),
                       "action": e.get("action"), "confidence": e.get("confidence"),
                       "strategy": e.get("strategy"), "evidence": e.get("evidence")}
                recorded_blocks.setdefault(float(e["ts"]), []).append(blk)
                recorded_by_ts_sym[(float(e["ts"]), e["symbol"])] = e
        for t in trades:
            # grid closes recorded at decision points let the rebuild mirror the
            # original run exactly; REPLAY_END rows are settlement artifacts, and
            # position exits are candle-driven (recomputed deterministically)
            if t.get("ts_close") and t.get("close_type") != "REPLAY_END" \
                    and t.get("strategy") == "grid_executor":
                recorded_closes.setdefault(float(t["ts_close"]), []).append(t)
        print(f"resuming {exp_id}: {len(events)} recorded decisions up to "
              f"{time.strftime('%m-%d %H:%M', time.gmtime(resume_last_ts))} UTC, "
              f"window start {time.strftime('%m-%d %H:%M', time.gmtime(start_ts))} UTC")
    warmup_ts = start_ts - 3 * 86400  # 3 extra days for context windows
    fetch_days = int((end_ts - start_ts) / 86400) + 4

    print(f"fetching history {time.strftime('%m-%d %H:%M', time.localtime(start_ts))}"
          f" -> {time.strftime('%m-%d %H:%M', time.localtime(end_ts))} ...")
    candles = {}
    funding = {}
    bar_interval = f"{args.bar_minutes}m"
    for sym in SYMBOLS:
        all_c = await fetch_candles(sym, fetch_days, interval=bar_interval)
        candles[sym] = all_c
        funding[sym] = await fetch_funding(sym, warmup_ts * 1000)
        print(f"  {sym}: {len(all_c)} candles, {len(funding[sym])} funding points")

    llm = create_llm_client()
    versions = current_versions()
    import yaml
    with open(REPO_ROOT / "config" / "settings.yaml", "r", encoding="utf-8") as fh:
        settings = yaml.safe_load(fh)
    if args.max_total_exposure is not None:
        settings.setdefault("risk", {})["max_total_exposure_quote"] = args.max_total_exposure
    if args.max_symbol_exposure is not None:
        settings.setdefault("risk", {})["max_symbol_exposure_quote"] = args.max_symbol_exposure
    cost_validator = CostValidator.from_config(settings)
    risk_validator = RiskValidator.from_config(settings)
    margin = args.position_margin
    leverage = args.leverage
    notional = margin * leverage
    risk_note = (f"lev={leverage:g}x margin={margin:g} notional={notional:g} "
                 f"tp={args.tp_pct:g} sl={args.sl_pct:g} sl_atr_mult={args.sl_atr_mult:g} "
                 f"rr={args.rr:g} breakeven={not args.no_breakeven} "
                 f"trailing={not args.no_trailing} "
                 f"time_stop_h={args.time_stop_hours:g} "
                 f"risk_per_trade={args.risk_per_trade_pct:g} "
                 f"cooldown_h={args.cooldown_hours:g} "
                 f"xs={'on' if args.cross_section else 'off'}(top{args.top_n},"
                 f"disp>={args.min_dispersion:g},samedir={args.same_dir_discount:g}) "
                 f"min_conf={args.min_confidence:g}")
    if exp_id is None:
        exp_id = store.start_experiment(
            name=f"bt-replay-{args.days}d"
                 + ("" if args.cross_section else "-noxs"),
            symbols=SYMBOLS,
            strategies=["grid", "position"],
            notes=f"historical forward-replay backtest; indicative only; "
                  f"model={llm.model}; {risk_note}",
            status="backtest",  # never 'running' — the live agent loop tags
                                # decisions with the active 'running' experiment
            **versions,
        )
    print(f"replay experiment: {exp_id} ({risk_note})")

    system = (REPO_ROOT / "agent" / "prompts" / "trading_manager.md") \
        .read_text(encoding="utf-8").replace("{{MODE}}", "PAPER") + REPLAY_ADDENDUM

    cash = 10_000.0
    positions: list[SimPosition] = []
    grids: dict[str, SimGrid] = {}
    equity_curve: list[dict] = []
    trade_count = 0
    consecutive_llm_errors = 0
    cooldown_until: dict[tuple[str, str], float] = {}  # (symbol, side) -> ts

    timeline = [c["timestamp"] for c in candles["BTC"] if start_ts <= c["timestamp"] <= end_ts]
    by_ts = {s: {c["timestamp"]: c for c in candles[s]} for s in SYMBOLS}
    boundary_printed = False
    decision_step = max(1, round(args.decision_minutes / args.bar_minutes))
    bph = max(1, 60 // args.bar_minutes)  # bars per hour: keeps grid-protection
                                          # and baseline windows time-constant

    for i, ts in enumerate(timeline):
        prefix = resume_last_ts is not None and ts <= resume_last_ts
        # 1. advance simulations with this candle
        for pos in list(positions):
            c = by_ts.get(pos.symbol, {}).get(ts)
            if not c:
                continue
            result = pos.check_exit(c, ts)
            if result:
                pnl, reason, fees = result
                cash += pos.margin + pnl
                positions.remove(pos)
                if reason in ("STOP_LOSS", "LIQUIDATION") and args.cooldown_hours > 0:
                    cooldown_until[(pos.symbol, pos.side)] = ts + args.cooldown_hours * 3600
                if not prefix:
                    store.upsert_trade_event(
                        executor_id=pos.executor_id, ts_open=pos.opened_ts, ts_close=ts,
                        symbol=pos.symbol, strategy="position_executor", side=pos.side,
                        status="CLOSED", close_type=reason, pnl_quote=pnl,
                        gross_pnl=pnl + fees, fees_quote=fees,
                        entry_fee=fees / 2, exit_fee=fees / 2,
                        regime_at_entry=pos.regime_at_entry, source="agent",
                        experiment_id=exp_id)
        for sym, g in list(grids.items()):
            c = by_ts.get(sym, {}).get(ts)
            if c:
                g.sim.process_candle(c)

        # 2. equity snapshot
        px = {s: float(by_ts[s].get(ts, {}).get("close", 0) or 0)
              for s in SYMBOLS}
        pos_val = sum(
            p.margin
            + (px[p.symbol] - p.entry) * p.qty * (1 if p.side == "LONG" else -1)
            for p in positions if px.get(p.symbol))
        geq = sum(g.sim.equity(px[s]) for s, g in grids.items() if px.get(s))
        equity = cash + pos_val + geq
        equity_curve.append({"ts": ts, "equity": equity})

        # 3. decision point
        if prefix:
            # rebuild follows the recorded decision timestamps exactly — the
            # original run stepped by candle index, which drifts off the
            # decision phase when candles are missing, so index alignment
            # is impossible
            if float(ts) not in recorded_blocks and float(ts) not in recorded_closes:
                continue
        elif resume_last_ts is not None:
            if (ts - resume_last_ts) % (args.decision_minutes * 60) != 0:
                continue
        elif i % decision_step != 0:
            continue
        fr = {s: max((f for f in funding[s] if f <= ts), default=None) for s in SYMBOLS}
        fr = {s: (funding[s][f] if f else None) for s, f in fr.items()}
        summaries = {s: compute_market_features(candles[s], upto_ts=ts,
                                                funding_rate=fr[s],
                                                bar_minutes=args.bar_minutes)
                     for s in SYMBOLS}
        if any(v is None for v in summaries.values()):
            continue
        # v0.6 L1: deterministic cross-sectional ranking over the watchlist.
        # The LLM never ranks — it may only time entries inside the
        # candidate sets this layer produces (design doc §3.1)
        xs = None
        if args.cross_section:
            xs = score_symbols(summaries, top_n=args.top_n,
                               min_dispersion=args.min_dispersion,
                               funding=fr)
        context = {
            "current_time_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)),
            "markets": summaries,
            "cross_section": ({k: xs[k] for k in
                               ("ranking", "scores", "dispersion", "rotation",
                                "long_candidates", "short_candidates")}
                              if xs else None),
            "portfolio": {
                "cash": round(cash, 2),
                "open_positions": [
                    {"symbol": p.symbol, "side": p.side, "entry": p.entry}
                    for p in positions],
                "open_grids": list(grids.keys()),
                "equity": round(equity, 2),
            },
        }
        if prefix:
            # rebuild phase: replay recorded decisions without LLM calls
            blocks = recorded_blocks.get(float(ts), [])
            for cl in recorded_closes.get(float(ts), []):
                csym = cl["symbol"]
                if csym in grids:
                    g = grids.pop(csym)
                    cash += g.sim.equity(px.get(csym) or summaries[csym]["price"])
        else:
            if resume_last_ts is not None and not boundary_printed:
                boundary_printed = True
                print(f"  [resume boundary] cash {cash:.2f}, "
                      f"positions {len(positions)}, grids {list(grids)}")
            user_msg = (
                "Historical replay. Data as of the stated time:\n"
                + json.dumps(context, ensure_ascii=False)
                + f"\nAnalyze all {len(SYMBOLS)} symbols and output one "
                  "analysis block per symbol. The cross_section block is a "
                  "deterministic ranking you CANNOT override: you may only "
                  "propose LONG for long_candidates and SHORT for "
                  "short_candidates; every other symbol is WAIT/WATCH."
            )
            decision_id = new_decision_id()
            try:
                resp = await llm.create(system=system, tools=[],
                                        messages=[{"role": "user", "content": user_msg}])
                answer = resp.text
                consecutive_llm_errors = 0
            except Exception as exc:
                msg = str(exc)
                print(f"  [{time.strftime('%m-%d %H:%M', time.gmtime(ts))}] LLM error: {exc}")
                consecutive_llm_errors += 1
                fatal = any(s in msg for s in ("402", "Insufficient Balance",
                                               "401", "Authentication", "invalid_api_key"))
                if fatal or consecutive_llm_errors >= 10:
                    store.end_experiment(exp_id, status="failed")
                    store.close()
                    raise SystemExit(
                        f"fatal LLM error after {consecutive_llm_errors} consecutive failures; "
                        f"experiment {exp_id} marked failed: {msg[:200]}")
                continue
            blocks = extract_analysis_blocks(answer)
            if not blocks:
                store.record_decision_event(
                    decision_id=decision_id, mode="BACKTEST", action="SCAN",
                    outcome_status="NO_BLOCK", market_context=context,
                    portfolio_context=context["portfolio"], experiment_id=exp_id,
                    llm_model=llm.model,
                    **versions)
                continue
        for block in blocks:
            sym = str(block.get("symbol", "")).upper()
            if sym not in SYMBOLS:
                continue
            regime = normalize_regime(block.get("regime"))
            action = str(block.get("action") or "WAIT").upper()
            conf = float(block.get("confidence") or 0)
            price = px.get(sym) or summaries[sym]["price"]
            if prefix:
                # mirror the recorded outcome instead of re-validating, so the
                # rebuild matches the original run even if config has drifted
                rec = recorded_by_ts_sym.get((float(ts), sym)) or {}
                eid = rec.get("execution_id") or ""
                if rec.get("outcome_status") == "EXECUTED":
                    if eid.startswith("bt-grid-") and sym not in grids:
                        grids[sym] = SimGrid(
                            GridSim(price * (1 - GRID_RANGE_PCT),
                                    price * (1 + GRID_RANGE_PCT),
                                    args.grid_size, tp=GRID_TP),
                            sym, ts, eid, rec.get("market_regime"))
                        cash -= args.grid_size
                        trade_count += 1
                    elif eid.startswith("bt-pos-") \
                            and not any(p.symbol == sym for p in positions):
                        tp_eff, sl_eff = position_barriers(
                            summaries[sym].get("atr_14bar_pct"), args,
                            bar_seconds=args.bar_minutes * 60)
                        positions.append(SimPosition(
                            sym, action, price, notional / price,
                            ts + POS_TIME_LIMIT_S, ts, eid,
                            rec.get("market_regime"),
                            margin=margin, leverage=leverage,
                            tp_pct=tp_eff, sl_pct=sl_eff,
                            breakeven=not args.no_breakeven,
                            trailing=not args.no_trailing,
                            time_stop_s=args.time_stop_hours * 3600))
                        cash -= margin
                        trade_count += 1
                continue
            execution_id = None
            risk_status = "PASSED"
            rejection_reason = None

            # objective features for the grid protection state machine —
            # resampled back to 1h so its tuned EMA/ATR windows keep their
            # original time semantics regardless of the decision bar size
            sym_candles = _resample(
                [c for c in candles[sym] if c["timestamp"] <= ts], bph)[-120:]
            grid_state = evaluate_grid_state(
                compute_grid_features(sym_candles), regime=regime, config=settings)

            # deterministic execution policy — proposals pass through the
            # same cost/risk/protection gates as the live path (v0.4)
            if block.get("strategy") == "grid" and regime == "RANGING" and sym not in grids:
                rejection = None
                if grid_state.state in (GRID_DEFENSIVE, GRID_EXIT):
                    rejection = ("GRID_REJECTED",
                                 f"{grid_state.state}: {'; '.join(grid_state.reasons)}")
                else:
                    cost = cost_validator.validate_grid(args.grid_size, GRID_TP,
                                                        maker_entry=True, maker_exit=True)
                    if not cost.approved:
                        rejection = ("COST_REJECTED", cost.summary())
                if rejection is None:
                    risk = risk_validator.validate(
                        symbol=sym, new_exposure_quote=args.grid_size,
                        current_exposure_quote=args.grid_size * len(grids)
                        + sum(p.margin * p.leverage for p in positions),
                        symbol_exposure_quote=0.0)
                    if not risk.approved:
                        rejection = ("RISK_REJECTED", "; ".join(risk.reasons))
                if rejection:
                    risk_status, rejection_reason = rejection
                else:
                    execution_id = f"bt-grid-{sym}-{int(ts)}"
                    grids[sym] = SimGrid(
                        GridSim(price * (1 - GRID_RANGE_PCT), price * (1 + GRID_RANGE_PCT),
                                args.grid_size, tp=GRID_TP),
                        sym, ts, execution_id, regime)
                    cash -= args.grid_size
                    trade_count += 1
            elif (block.get("strategy") != "grid" and sym in grids) \
                    or (sym in grids and grid_state.state == GRID_EXIT):
                # regime-based exit (LLM) or deterministic protection exit
                close_type = "GRID_PROTECTION_EXIT" \
                    if sym in grids and grid_state.state == GRID_EXIT else "REGIME_EXIT"
                g = grids.pop(sym)
                cash += g.sim.equity(price)
                closed_pnl = g.sim.equity(price) - args.grid_size
                store.upsert_trade_event(
                    executor_id=g.executor_id, ts_open=g.opened_ts, ts_close=ts,
                    symbol=sym, strategy="grid_executor", status="CLOSED",
                    close_type=close_type, pnl_quote=closed_pnl,
                    fees_quote=g.sim.fees, gross_pnl=closed_pnl + g.sim.fees,
                    regime_at_entry=g.regime_at_entry,
                    source="agent", experiment_id=exp_id)
            if action in ("LONG", "SHORT") and conf >= args.min_confidence \
                    and not any(p.symbol == sym for p in positions):
                # 0. cross-section gate (framework v0.6): LONG only for top-N
                #    relative-strength candidates, SHORT only for the bottom
                #    rank in a confirmed downtrend. Ranking is deterministic —
                #    the LLM cannot talk its way past it.
                xs_reject = None
                if xs is not None:
                    cands = xs["long_candidates"] if action == "LONG" \
                        else xs["short_candidates"]
                    if sym not in cands:
                        rank = (xs["ranking"].index(sym) + 1
                                if sym in xs["ranking"] else 0)
                        xs_reject = (f"cross-section: {sym} rank {rank}/"
                                     f"{len(xs['ranking'])} not in "
                                     f"{'long' if action == 'LONG' else 'short'} "
                                     f"candidates {cands} "
                                     f"(dispersion {xs['dispersion']:.2f})")
                # 1. deterministic entry gate (framework v0.5): direction
                #    must align with 8h momentum / EMA cross / RSI / volume;
                #    stop-out cooldown blocks re-entry into the same chop
                if xs_reject:
                    risk_status, rejection_reason = "ENTRY_REJECTED", xs_reject
                elif not (gate := validate_entry(summaries[sym], action, regime,
                                                 config=settings)).approved:
                    risk_status = "ENTRY_REJECTED"
                    rejection_reason = "; ".join(gate.reasons)
                elif ts < cooldown_until.get((sym, action), 0):
                    risk_status = "COOLDOWN"
                    rejection_reason = (f"{sym} {action} in post-stop cooldown "
                                        f"({args.cooldown_hours:g}h)")
                else:
                    tp_eff, sl_eff = position_barriers(
                        summaries[sym].get("atr_14bar_pct"), args,
                        bar_seconds=args.bar_minutes * 60)
                    margin_eff = position_margin_for(equity, sl_eff, args)
                    if xs is not None:
                        # portfolio layer (v0.6 L3): same-direction positions
                        # on correlated coins are ONE bet — each additional
                        # one gets a discounted risk budget; the short side
                        # gets half (long-leg concentration, frontier §7)
                        k_same = sum(1 for p in positions if p.side == action)
                        if k_same:
                            margin_eff *= args.same_dir_discount ** k_same
                        if action == "SHORT":
                            margin_eff *= 0.5
                    notional_eff = margin_eff * leverage
                    if cash < margin_eff:
                        risk_status, rejection_reason = "RISK_REJECTED", "insufficient cash"
                    else:
                        # funding is charged when the position would pay it
                        # over its intended holding window (never credited);
                        # fr[sym] is the latest funding rate known at ts
                        cost = cost_validator.validate_position(
                            notional_eff, tp_eff,
                            funding_rate=(fr.get(sym) if fr else None),
                            expected_hold_hours=max(args.time_stop_hours, 1.0),
                            side=action)
                        if not cost.approved:
                            risk_status, rejection_reason = "COST_REJECTED", cost.summary()
                        else:
                            risk = risk_validator.validate(
                                symbol=sym, new_exposure_quote=notional_eff,
                                current_exposure_quote=args.grid_size * len(grids)
                                + sum(p.margin * p.leverage for p in positions),
                                symbol_exposure_quote=sum(
                                    p.margin * p.leverage for p in positions
                                    if p.symbol == sym))
                            if not risk.approved:
                                risk_status, rejection_reason = "RISK_REJECTED", "; ".join(risk.reasons)
                            else:
                                execution_id = f"bt-pos-{sym}-{int(ts)}"
                                positions.append(SimPosition(
                                    sym, action, price, notional_eff / price,
                                    ts + POS_TIME_LIMIT_S, ts, execution_id, regime,
                                    margin=margin_eff, leverage=leverage,
                                    tp_pct=tp_eff, sl_pct=sl_eff,
                                    breakeven=not args.no_breakeven,
                                    trailing=not args.no_trailing,
                                    time_stop_s=args.time_stop_hours * 3600))
                                cash -= margin_eff
                                trade_count += 1

            store.record_decision_event(
                decision_id=decision_id if sym == SYMBOLS[0] else new_decision_id(),
                ts=float(ts), mode="BACKTEST", symbol=sym, market_regime=regime,
                action=action, strategy=block.get("strategy"), confidence=conf,
                evidence=block.get("evidence"), risk_status=risk_status,
                outcome_status="EXECUTED" if execution_id else
                ("REJECTED" if rejection_reason else "RECORDED"),
                execution_id=execution_id, market_context=context["markets"].get(sym),
                portfolio_context=context["portfolio"], experiment_id=exp_id,
                rejection_reason=rejection_reason,
                llm_model=llm.model,
                **versions)
        if not prefix:
            print(f"  [{time.strftime('%m-%d %H:%M', time.gmtime(ts))}] "
                  + " ".join(f"{b.get('symbol')}:{b.get('action')}/{b.get('regime')}"
                             for b in blocks))
        elif i % 480 == 0:
            print(f"  [rebuild {time.strftime('%m-%d %H:%M', time.gmtime(ts))}] "
                  f"equity {equity:.2f}")

    # settle everything at the last price
    last_ts = timeline[-1]
    for pos in list(positions):
        price = px[pos.symbol]
        sign = 1 if pos.side == "LONG" else -1
        fees = (pos.entry + price) * pos.qty * FEE_RATE
        pnl = (price - pos.entry) * pos.qty * sign - fees
        cash += pos.margin + pnl
        store.upsert_trade_event(
            executor_id=pos.executor_id, ts_open=pos.opened_ts, ts_close=last_ts,
            symbol=pos.symbol, strategy="position_executor", side=pos.side,
            status="CLOSED", close_type="REPLAY_END", pnl_quote=pnl,
            gross_pnl=pnl + fees, fees_quote=fees,
            entry_fee=fees / 2, exit_fee=fees / 2,
            regime_at_entry=pos.regime_at_entry, source="agent", experiment_id=exp_id)
    for sym, g in list(grids.items()):
        price = px[sym]
        cash += g.sim.equity(price)
        grid_pnl = g.sim.equity(price) - args.grid_size
        store.upsert_trade_event(
            executor_id=g.executor_id, ts_open=g.opened_ts, ts_close=last_ts,
            symbol=sym, strategy="grid_executor", status="CLOSED",
            close_type="REPLAY_END", pnl_quote=grid_pnl,
            fees_quote=g.sim.fees, gross_pnl=grid_pnl + g.sim.fees,
            regime_at_entry=g.regime_at_entry,
            source="agent", experiment_id=exp_id)

    # baselines over the same window: fixed grid + fixed trend + buy&hold
    from analytics import metrics as m
    agent_eq = m.equity_metrics(equity_curve)
    baselines = {}
    for sym in SYMBOLS:
        window = [c for c in candles[sym] if start_ts <= c["timestamp"] <= end_ts]
        first, last = float(window[0]["close"]), float(window[-1]["close"])
        base = GridSim(first * 0.97, first * 1.03, args.grid_size, tp=GRID_TP)
        for c in window:
            base.process_candle(c)
        store.upsert_trade_event(
            executor_id=f"bt-baseline-grid-{sym}", ts_open=start_ts, ts_close=last_ts,
            symbol=sym, strategy="grid_executor", status="CLOSED",
            close_type="REPLAY_END", pnl_quote=base.equity(last) - args.grid_size,
            fees_quote=base.fees, source="baseline", experiment_id=exp_id)

        # fixed trend baseline: EMA20/EMA50 cross, same barriers as agent positions
        def _ema(vals, n):
            k = 2 / (n + 1)
            e = vals[0]
            for v in vals[1:]:
                e = v * k + e * (1 - k)
            return e
        trend_pnl, trend_trades, trend_pos = 0.0, 0, None
        closes_so_far: list[float] = []
        bars_so_far: list[dict] = []
        for c in window:
            closes_so_far.append(float(c["close"]))
            bars_so_far.append(c)
            if trend_pos is not None:
                result = trend_pos.check_exit(c, c["timestamp"])
                if result:
                    pnl, reason, fees = result
                    trend_pnl += pnl
                    trend_trades += 1
                    trend_pos = None
            elif len(closes_so_far) >= 80 * bph and \
                    _ema(closes_so_far[-40 * bph:], 20 * bph) > \
                    _ema(closes_so_far[-80 * bph:], 50 * bph):
                px_now = float(c["close"])
                # same ATR-scaled barrier policy as agent positions
                trs = [max(float(bars_so_far[j]["high"]) - float(bars_so_far[j]["low"]),
                           abs(float(bars_so_far[j]["high"]) - closes_so_far[j - 1]),
                           abs(float(bars_so_far[j]["low"]) - closes_so_far[j - 1]))
                       for j in range(max(1, len(bars_so_far) - 14), len(bars_so_far))]
                atr_pct = (sum(trs) / len(trs)) / px_now * 100 if trs else None
                tp_b, sl_b = position_barriers(atr_pct, args,
                                               bar_seconds=args.bar_minutes * 60)
                trend_pos = SimPosition(
                    sym, "LONG", px_now, notional / px_now,
                    c["timestamp"] + POS_TIME_LIMIT_S, c["timestamp"],
                    f"bt-baseline-trend-{sym}-{int(c['timestamp'])}", None,
                    margin=margin, leverage=leverage,
                    tp_pct=tp_b, sl_pct=sl_b,
                    breakeven=not args.no_breakeven,
                    trailing=not args.no_trailing,
                    time_stop_s=args.time_stop_hours * 3600)
        if trend_pos is not None:
            sign = 1
            fees = (trend_pos.entry + last) * trend_pos.qty * FEE_RATE
            trend_pnl += (last - trend_pos.entry) * trend_pos.qty * sign - fees
            trend_trades += 1

        baselines[sym] = {
            "fixed_grid_pnl": round(base.equity(last) - args.grid_size, 4),
            "fixed_trend_pnl": round(trend_pnl, 4),
            "fixed_trend_trades": trend_trades,
            "buy_hold_pnl": round(args.grid_size * (last / first - 1), 4),
        }

    summary = {
        "replayed_window": {"start_ts": start_ts, "end_ts": end_ts},
        "start_equity": equity_curve[0]["equity"],
        "final_equity": equity_curve[-1]["equity"],
        "agent_equity_metrics": agent_eq,
        "trade_count": trade_count,
        "baselines": baselines,
        "baseline_notional": {"fixed_grid": args.grid_size, "trend": notional,
                              "buy_hold": args.grid_size},
        "risk_profile": {"leverage": leverage, "position_margin": margin,
                         "position_notional": notional, "tp_pct": args.tp_pct,
                         "sl_pct": args.sl_pct,
                         "sl_atr_mult": args.sl_atr_mult, "rr": args.rr,
                         "breakeven": not args.no_breakeven,
                         "trailing": not args.no_trailing,
                         "time_stop_hours": args.time_stop_hours,
                         "risk_per_trade_pct": args.risk_per_trade_pct,
                         "cooldown_hours": args.cooldown_hours,
                         "cross_section": args.cross_section,
                         "top_n": args.top_n,
                         "min_dispersion": args.min_dispersion,
                         "same_dir_discount": args.same_dir_discount,
                         "min_confidence": args.min_confidence,
                         "grid_size": args.grid_size,
                         "bar_minutes": args.bar_minutes,
                         "decision_minutes": args.decision_minutes},
    }
    if args.resume_exp:
        summary["resumed"] = True
        summary["resume_from_ts"] = resume_last_ts
    store.set_experiment_summary(exp_id, summary)
    store.end_experiment(exp_id)
    store.close()

    span_days = round((end_ts - start_ts) / 86400, 1)
    print("\n===== REPLAY RESULT =====")
    print(f"experiment: {exp_id} ({span_days}d, {args.bar_minutes}m bars, "
          f"decisions every {args.decision_minutes}min)")
    print(f"agent:  final equity ${equity_curve[-1]['equity']:.2f}"
          f" (start $10,000) · return {agent_eq['total_return'] and round(agent_eq['total_return']*100, 2)}%"
          f" · maxDD {agent_eq['max_drawdown_pct'] and round(agent_eq['max_drawdown_pct'], 2)}%"
          f" · trades {trade_count}")
    for sym, b in baselines.items():
        print(f"baseline {sym}: fixed grid {b['fixed_grid_pnl']:+.2f}"
              f" · fixed trend {b['fixed_trend_pnl']:+.2f} ({b['fixed_trend_trades']} trades)"
              f" · buy&hold {b['buy_hold_pnl']:+.2f} (on ${args.grid_size:.0f})")
    print("note: unknown model training cutoff — contamination cannot be ruled out;")


if __name__ == "__main__":
    asyncio.run(main())
