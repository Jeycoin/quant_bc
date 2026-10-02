# Experiment Report: bt-replay-30d

- experiment_id: `exp-ac0ec11406f7`
- status: completed
- replayed window: 2026-09-03 17:00 → 2026-10-02 16:00 (29.0 days)
- executed (wall clock): 2026-10-02 17:13 → 2026-10-02 18:11
- symbols: ["BTC", "ETH"]
- strategies: ["grid", "position"]
- versions: agent=0.2.0 prompt=ea2abb0b config=444bc21c

## Market

- regimes observed (from decision events): BREAKOUT×24, HIGH_VOLATILITY×1, LOW_VOLATILITY×10, RANGING×485, REGIME_TRANSITION×34, TRENDING_BEAR×78, TRENDING_BULL×72, UNCERTAIN×2

## Agent

- decisions: 713 ({"LONG": 46, "SCAN": 7, "SHORT": 9, "WAIT": 191, "WATCH": 460})
- WAIT ratio: 26.79% · REJECT ratio: 0.00%
- trades executed: 67 of 67

## Strategy

| strategy | trades | closed | pnl | fees | win rate |
|---|---|---|---|---|---|
| position_executor | 37 | 37 | $12.9828 | $5.9292 | 43.24% |
| grid_executor | 28 | 28 | $3.9387 | $9.8888 | 57.14% |

## Market Regime Attribution

| regime | trades | closed | pnl | win rate |
|---|---|---|---|---|
| BREAKOUT | 8 | 8 | $16.3733 | 75.00% |
| RANGING | 28 | 28 | $3.9387 | 57.14% |
| TRENDING_BULL | 22 | 22 | $-0.2697 | 36.36% |
| TRENDING_BEAR | 7 | 7 | $-3.1208 | 28.57% |

## Execution

- filled volume: $0.0000
- total fees: $23.6391

## Risk

- max drawdown: —
- equity snapshots: 0
- replay equity: start $10,000.0000 → final $10,016.9215 · return 0.17% · maxDD 0.23% · sharpe-like 0.16688308288637105

## AI Decision Quality

- reviews: 0 ({})

## Comparison: AI Assisted vs Baseline

| metric | AI assisted | baseline |
|---|---|---|
| trades | 65 | 2 |
| total pnl | $16.9215 | $11.7122 |
| win rate | 49.23% | 100.00% |
| profit factor | 1.3170862625583872 | — |
| expectancy | $0.2603 | $5.8561 |
| avg holding | 12.2h | 719.8h |

### Replay baselines (same window)

| symbol | fixed grid | fixed trend | buy & hold |
|---|---|---|---|
| BTC | $4.9367 | $4.0464 (24 trades) | $22.2185 |
| ETH | $11.0326 | $5.9212 (37 trades) | $26.2244 |

> Caution (§36): a higher AI PnL does not prove AI is better — consider sample size, period, market regime, fees, slippage and parameter differences before drawing conclusions.
