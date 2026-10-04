# Experiment Report: bt-replay-90d

- experiment_id: `exp-23f2df348a8e`
- status: completed
- replayed window: 2026-07-05 16:00 → 2026-10-04 16:15 (91.0 days)
- executed (wall clock): 2026-10-03 15:47 → 2026-10-04 16:21
- symbols: ["BTC", "ETH"]
- strategies: ["grid", "position"]
- versions: agent=0.2.0 prompt=ea2abb0b config=444bc21c

## Market

- regimes observed (from decision events): BREAKOUT×77, HIGH_VOLATILITY×10, LOW_VOLATILITY×51, RANGING×1469, REGIME_TRANSITION×91, TRENDING_BEAR×198, TRENDING_BULL×195, UNCERTAIN×14

## Agent

- decisions: 2144 ({"LONG": 121, "SCAN": 39, "SHORT": 25, "WAIT": 589, "WATCH": 1370})
- WAIT ratio: 27.47% · REJECT ratio: 0.00%
- trades executed: 184 of 184

## Strategy

| strategy | trades | closed | pnl | fees | win rate |
|---|---|---|---|---|---|
| position_executor | 97 | 97 | $-1.9746 | $15.5387 | 39.18% |
| grid_executor | 85 | 85 | $-11.8743 | $29.2056 | 49.41% |

## Market Regime Attribution

| regime | trades | closed | pnl | win rate |
|---|---|---|---|---|
| BREAKOUT | 37 | 37 | $34.0539 | 54.05% |
| REGIME_TRANSITION | 3 | 3 | $-0.0297 | 66.67% |
| RANGING | 86 | 86 | $-8.0359 | 50.00% |
| TRENDING_BULL | 40 | 40 | $-18.6481 | 32.50% |
| TRENDING_BEAR | 16 | 16 | $-21.1892 | 12.50% |

## Execution

- filled volume: $0.0000
- total fees: $61.5878

## Risk

- max drawdown: —
- equity snapshots: 0
- replay equity: start $10,000.0000 → final $9,986.1511 · return -0.14% · maxDD 0.42% · sharpe-like -0.04047995833468207

## AI Decision Quality

- reviews: 0 ({})

## Comparison: AI Assisted vs Baseline

| metric | AI assisted | baseline |
|---|---|---|
| trades | 182 | 2 |
| total pnl | $-13.8489 | $25.2232 |
| win rate | 43.96% | 100.00% |
| profit factor | 0.9181181269882124 | — |
| expectancy | $-0.0761 | $12.6116 |
| avg holding | 13.2h | 2184.0h |

### Replay baselines (same window)

| symbol | fixed grid | fixed trend | buy & hold |
|---|---|---|---|
| BTC | $17.1486 | $-16.4872 (96 trades) | $70.6204 |
| ETH | $8.0746 | $-28.4442 (133 trades) | $105.6215 |

> Caution (§36): a higher AI PnL does not prove AI is better — consider sample size, period, market regime, fees, slippage and parameter differences before drawing conclusions.
