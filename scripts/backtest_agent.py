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
  directional position when action=LONG/SHORT with confidence >= 0.6

Usage:
  python scripts/backtest_agent.py [--days 7] [--decision-hours 2]
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
from agent.validators import (
    GRID_DEFENSIVE,
    GRID_EXIT,
    GRID_WARNING,
    CostValidator,
    RiskValidator,
    compute_grid_features,
    evaluate_grid_state,
)
from analytics.store import AnalyticsStore, new_decision_id
from analytics.versioning import current_versions
from backtest_grid import FEE_RATE, GridSim, fetch_candles
from integrations.llm import create_llm_client

SYMBOLS = ["BTC", "ETH"]
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
knowledge of what happened after the stated time. Simulated execution
policy: strategy "grid" in a ranging regime opens a grid; LONG/SHORT with
confidence >= 0.6 opens a directional position; WAIT/WATCH does nothing.
End your reply with one ```analysis block per symbol.
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

    def check_exit(self, c: dict, ts: float) -> tuple[float, str, float] | None:
        """Returns (net_pnl, reason, total_fees) or None."""
        high, low, close = float(c["high"]), float(c["low"]), float(c["close"])
        tp = self.entry * (1 + POS_TP_PCT) if self.side == "LONG" else self.entry * (1 - POS_TP_PCT)
        sl = self.entry * (1 - POS_SL_PCT) if self.side == "LONG" else self.entry * (1 + POS_SL_PCT)
        sign = 1 if self.side == "LONG" else -1
        exit_price = None
        reason = ""
        if self.side == "LONG" and low <= sl:
            exit_price, reason = sl, "STOP_LOSS"
        elif self.side == "SHORT" and high >= sl:
            exit_price, reason = sl, "STOP_LOSS"
        elif self.side == "LONG" and high >= tp:
            exit_price, reason = tp, "TAKE_PROFIT"
        elif self.side == "SHORT" and low <= tp:
            exit_price, reason = tp, "TAKE_PROFIT"
        elif ts >= self.deadline:
            exit_price, reason = close, "TIME_LIMIT"
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


def _summarize(candles: list[dict], upto_ts: float) -> dict | None:
    past = [c for c in candles if c["timestamp"] <= upto_ts]
    if len(past) < 48:
        return None
    closes = [float(c["close"]) for c in past]
    last24 = past[-24:]
    last48 = past[-48:]
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(-48, 0)]
    vol = (sum((r - sum(rets) / len(rets)) ** 2 for r in rets) / len(rets)) ** 0.5
    return {
        "price": closes[-1],
        "24h_change_pct": round((closes[-1] / closes[-25] - 1) * 100, 2),
        "24h_high": max(float(c["high"]) for c in last24),
        "24h_low": min(float(c["low"]) for c in last24),
        "48h_high": max(float(c["high"]) for c in last48),
        "48h_low": min(float(c["low"]) for c in last48),
        "hourly_vol_pct": round(vol * 100, 3),
        "24h_volume": round(sum(float(c["volume"]) for c in last24), 1),
        "last_12_closes": [round(x, 2) for x in closes[-12:]],
    }


async def fetch_funding(coin: str, start_ms: int) -> dict[float, float]:
    async with httpx.AsyncClient(base_url="https://api.hyperliquid.xyz",
                                 timeout=30.0) as client:
        resp = await client.post("/info", json={
            "type": "fundingHistory", "coin": coin, "startTime": start_ms,
        })
        resp.raise_for_status()
        return {f["time"] / 1000: float(f["fundingRate"]) for f in resp.json()}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Agent replay backtest")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--decision-hours", type=int, default=2)
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    end_ts = int(time.time())
    start_ts = end_ts - args.days * 86400
    warmup_ts = start_ts - 3 * 86400  # 3 extra days for context windows

    print(f"fetching history {time.strftime('%m-%d %H:%M', time.localtime(start_ts))}"
          f" -> {time.strftime('%m-%d %H:%M', time.localtime(end_ts))} ...")
    candles = {}
    funding = {}
    for sym in SYMBOLS:
        all_c = await fetch_candles(sym, args.days + 3)
        candles[sym] = all_c
        funding[sym] = await fetch_funding(sym, warmup_ts * 1000)
        print(f"  {sym}: {len(all_c)} candles, {len(funding[sym])} funding points")

    llm = create_llm_client()
    store = AnalyticsStore(str(REPO_ROOT / "data" / "analytics.db"))
    versions = current_versions()
    import yaml
    with open(REPO_ROOT / "config" / "settings.yaml", "r", encoding="utf-8") as fh:
        settings = yaml.safe_load(fh)
    cost_validator = CostValidator.from_config(settings)
    risk_validator = RiskValidator.from_config(settings)
    exp_id = store.start_experiment(
        name=f"bt-replay-{args.days}d",
        symbols=SYMBOLS,
        strategies=["grid", "position"],
        notes=f"historical forward-replay backtest; indicative only; model={llm.model}",
        status="backtest",  # never 'running' — the live agent loop tags
                            # decisions with the active 'running' experiment
        **versions,
    )
    print(f"replay experiment: {exp_id}")

    system = (REPO_ROOT / "agent" / "prompts" / "trading_manager.md") \
        .read_text(encoding="utf-8").replace("{{MODE}}", "PAPER") + REPLAY_ADDENDUM

    cash = 10_000.0
    positions: list[SimPosition] = []
    grids: dict[str, SimGrid] = {}
    equity_curve: list[dict] = []
    trade_count = 0

    timeline = [c["timestamp"] for c in candles["BTC"] if start_ts <= c["timestamp"] <= end_ts]
    btc_by_ts = {c["timestamp"]: c for c in candles["BTC"]}
    eth_by_ts = {c["timestamp"]: c for c in candles["ETH"]}

    for i, ts in enumerate(timeline):
        # 1. advance simulations with this candle
        for pos in list(positions):
            c = (btc_by_ts if pos.symbol == "BTC" else eth_by_ts).get(ts)
            if not c:
                continue
            result = pos.check_exit(c, ts)
            if result:
                pnl, reason, fees = result
                cash += POSITION_SIZE + pnl
                positions.remove(pos)
                store.upsert_trade_event(
                    executor_id=pos.executor_id, ts_open=pos.opened_ts, ts_close=ts,
                    symbol=pos.symbol, strategy="position_executor", side=pos.side,
                    status="CLOSED", close_type=reason, pnl_quote=pnl,
                    gross_pnl=pnl + fees, fees_quote=fees,
                    entry_fee=fees / 2, exit_fee=fees / 2,
                    regime_at_entry=pos.regime_at_entry, source="agent",
                    experiment_id=exp_id)
        for sym, g in list(grids.items()):
            c = (btc_by_ts if sym == "BTC" else eth_by_ts).get(ts)
            if c:
                g.sim.process_candle(c)

        # 2. equity snapshot
        px = {s: float((btc_by_ts if s == "BTC" else eth_by_ts).get(ts, {}).get("close", 0) or 0)
              for s in SYMBOLS}
        pos_val = sum(
            POSITION_SIZE
            + (px[p.symbol] - p.entry) * p.qty * (1 if p.side == "LONG" else -1)
            for p in positions if px.get(p.symbol))
        geq = sum(g.sim.equity(px[s]) for s, g in grids.items() if px.get(s))
        equity = cash + pos_val + geq
        equity_curve.append({"ts": ts, "equity": equity})

        # 3. decision point
        if i % args.decision_hours != 0:
            continue
        summaries = {s: _summarize(candles[s], ts) for s in SYMBOLS}
        if any(v is None for v in summaries.values()):
            continue
        fr = {s: max((f for f in funding[s] if f <= ts), default=None) for s in SYMBOLS}
        fr = {s: (funding[s][f] if f else None) for s, f in fr.items()}
        context = {
            "current_time_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)),
            "markets": {s: {**summaries[s], "funding_rate": fr[s]} for s in SYMBOLS},
            "portfolio": {
                "cash": round(cash, 2),
                "open_positions": [
                    {"symbol": p.symbol, "side": p.side, "entry": p.entry}
                    for p in positions],
                "open_grids": list(grids.keys()),
                "equity": round(equity, 2),
            },
        }
        user_msg = (
            "Historical replay. Data as of the stated time:\n"
            + json.dumps(context, ensure_ascii=False)
            + "\nAnalyze both symbols and output one analysis block per symbol."
        )
        decision_id = new_decision_id()
        try:
            resp = await llm.create(system=system, tools=[],
                                    messages=[{"role": "user", "content": user_msg}])
            answer = resp.text
        except Exception as exc:
            print(f"  [{time.strftime('%m-%d %H:%M', time.gmtime(ts))}] LLM error: {exc}")
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
            execution_id = None
            risk_status = "PASSED"
            rejection_reason = None

            # objective features for the grid protection state machine
            sym_candles = [c for c in candles[sym] if c["timestamp"] <= ts][-120:]
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
                    cost = cost_validator.validate_grid(GRID_SIZE, GRID_TP,
                                                        maker_entry=True, maker_exit=True)
                    if not cost.approved:
                        rejection = ("COST_REJECTED", cost.summary())
                if rejection is None:
                    risk = risk_validator.validate(
                        symbol=sym, new_exposure_quote=GRID_SIZE,
                        current_exposure_quote=GRID_SIZE * len(grids)
                        + POSITION_SIZE * len(positions),
                        symbol_exposure_quote=0.0)
                    if not risk.approved:
                        rejection = ("RISK_REJECTED", "; ".join(risk.reasons))
                if rejection:
                    risk_status, rejection_reason = rejection
                else:
                    execution_id = f"bt-grid-{sym}-{int(ts)}"
                    grids[sym] = SimGrid(
                        GridSim(price * (1 - GRID_RANGE_PCT), price * (1 + GRID_RANGE_PCT),
                                GRID_SIZE, tp=GRID_TP),
                        sym, ts, execution_id, regime)
                    cash -= GRID_SIZE
                    trade_count += 1
            elif (block.get("strategy") != "grid" and sym in grids) \
                    or (sym in grids and grid_state.state == GRID_EXIT):
                # regime-based exit (LLM) or deterministic protection exit
                close_type = "GRID_PROTECTION_EXIT" \
                    if sym in grids and grid_state.state == GRID_EXIT else "REGIME_EXIT"
                g = grids.pop(sym)
                cash += g.sim.equity(price)
                closed_pnl = g.sim.equity(price) - GRID_SIZE
                store.upsert_trade_event(
                    executor_id=g.executor_id, ts_open=g.opened_ts, ts_close=ts,
                    symbol=sym, strategy="grid_executor", status="CLOSED",
                    close_type=close_type, pnl_quote=closed_pnl,
                    fees_quote=g.sim.fees, gross_pnl=closed_pnl + g.sim.fees,
                    regime_at_entry=g.regime_at_entry,
                    source="agent", experiment_id=exp_id)
            if action in ("LONG", "SHORT") and conf >= MIN_CONFIDENCE \
                    and not any(p.symbol == sym for p in positions) and cash >= POSITION_SIZE:
                cost = cost_validator.validate_position(POSITION_SIZE, POS_TP_PCT)
                if not cost.approved:
                    risk_status, rejection_reason = "COST_REJECTED", cost.summary()
                else:
                    risk = risk_validator.validate(
                        symbol=sym, new_exposure_quote=POSITION_SIZE,
                        current_exposure_quote=GRID_SIZE * len(grids)
                        + POSITION_SIZE * len(positions),
                        symbol_exposure_quote=POSITION_SIZE *
                        sum(1 for p in positions if p.symbol == sym))
                    if not risk.approved:
                        risk_status, rejection_reason = "RISK_REJECTED", "; ".join(risk.reasons)
                    else:
                        execution_id = f"bt-pos-{sym}-{int(ts)}"
                        positions.append(SimPosition(
                            sym, action, price, POSITION_SIZE / price,
                            ts + POS_TIME_LIMIT_S, ts, execution_id, regime))
                        cash -= POSITION_SIZE
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
        print(f"  [{time.strftime('%m-%d %H:%M', time.gmtime(ts))}] "
              + " ".join(f"{b.get('symbol')}:{b.get('action')}/{b.get('regime')}"
                         for b in blocks))

    # settle everything at the last price
    last_ts = timeline[-1]
    for pos in list(positions):
        price = px[pos.symbol]
        sign = 1 if pos.side == "LONG" else -1
        fees = (pos.entry + price) * pos.qty * FEE_RATE
        pnl = (price - pos.entry) * pos.qty * sign - fees
        cash += POSITION_SIZE + pnl
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
        grid_pnl = g.sim.equity(price) - GRID_SIZE
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
        base = GridSim(first * 0.97, first * 1.03, GRID_SIZE, tp=GRID_TP)
        for c in window:
            base.process_candle(c)
        store.upsert_trade_event(
            executor_id=f"bt-baseline-grid-{sym}", ts_open=start_ts, ts_close=last_ts,
            symbol=sym, strategy="grid_executor", status="CLOSED",
            close_type="REPLAY_END", pnl_quote=base.equity(last) - GRID_SIZE,
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
        for c in window:
            closes_so_far.append(float(c["close"]))
            if trend_pos is not None:
                result = trend_pos.check_exit(c, c["timestamp"])
                if result:
                    pnl, reason, fees = result
                    trend_pnl += pnl
                    trend_trades += 1
                    trend_pos = None
            elif len(closes_so_far) >= 80 and \
                    _ema(closes_so_far[-40:], 20) > _ema(closes_so_far[-80:], 50):
                px_now = float(c["close"])
                trend_pos = SimPosition(
                    sym, "LONG", px_now, POSITION_SIZE / px_now,
                    c["timestamp"] + POS_TIME_LIMIT_S, c["timestamp"],
                    f"bt-baseline-trend-{sym}-{int(c['timestamp'])}", None)
        if trend_pos is not None:
            sign = 1
            fees = (trend_pos.entry + last) * trend_pos.qty * FEE_RATE
            trend_pnl += (last - trend_pos.entry) * trend_pos.qty * sign - fees
            trend_trades += 1

        baselines[sym] = {
            "fixed_grid_pnl": round(base.equity(last) - GRID_SIZE, 4),
            "fixed_trend_pnl": round(trend_pnl, 4),
            "fixed_trend_trades": trend_trades,
            "buy_hold_pnl": round(GRID_SIZE * (last / first - 1), 4),
        }

    store.set_experiment_summary(exp_id, {
        "replayed_window": {"start_ts": start_ts, "end_ts": end_ts},
        "start_equity": equity_curve[0]["equity"],
        "final_equity": equity_curve[-1]["equity"],
        "agent_equity_metrics": agent_eq,
        "trade_count": trade_count,
        "baselines": baselines,
        "baseline_notional": {"fixed_grid": GRID_SIZE, "trend": POSITION_SIZE,
                              "buy_hold": GRID_SIZE},
    })
    store.end_experiment(exp_id)
    store.close()

    print("\n===== REPLAY RESULT =====")
    print(f"experiment: {exp_id} ({args.days}d, decisions every {args.decision_hours}h)")
    print(f"agent:  final equity ${equity_curve[-1]['equity']:.2f}"
          f" (start $10,000) · return {agent_eq['total_return'] and round(agent_eq['total_return']*100, 2)}%"
          f" · maxDD {agent_eq['max_drawdown_pct'] and round(agent_eq['max_drawdown_pct'], 2)}%"
          f" · trades {trade_count}")
    for sym, b in baselines.items():
        print(f"baseline {sym}: fixed grid {b['fixed_grid_pnl']:+.2f}"
              f" · fixed trend {b['fixed_trend_pnl']:+.2f} ({b['fixed_trend_trades']} trades)"
              f" · buy&hold {b['buy_hold_pnl']:+.2f} (on ${GRID_SIZE:.0f})")
    print("note: unknown model training cutoff — contamination cannot be ruled out;")


if __name__ == "__main__":
    asyncio.run(main())
