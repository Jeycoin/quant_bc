# Experiment Report: bt-replay-7d

- experiment_id: `exp-83cfc4b1ccdf`
- status: completed
- replayed window: 2026-09-28 23:24 → 2026-10-05 23:24 (7.0 days)
- executed (wall clock): 2026-10-05 23:24 → 2026-10-06 14:55
- symbols: ["BTC", "ETH"]
- strategies: ["grid", "position"]
- versions: agent=0.2.0 prompt=7b03c9c7 config=275ffbce

## Market

- regimes observed (from decision events): RANGING×297, REGIME_TRANSITION×4, TRENDING_BEAR×70, TRENDING_BULL×288, UNCERTAIN×7

## Agent

- decisions: 668 ({"CLOSE_SHORT": 1, "COVER_SHORT": 1, "HOLD": 17, "HOLD SHORT": 1, "HOLD_LONG": 11, "HOLD_SHORT": 7, "LONG": 93, "SCAN": 2, "SHORT": 31, "WAIT": 29, "WATCH": 473, "WATCH|HOLD_SHORT": 1, "WATCH|TAKE_PROFIT": 1})
- WAIT ratio: 4.34% · REJECT ratio: 0.00%
- trades executed: 25 of 25

## Strategy

| strategy | trades | closed | pnl | fees | win rate |
|---|---|---|---|---|---|
| grid_executor | 2 | 2 | $0.0070 | $0.0200 | 50.00% |
| position_executor | 21 | 21 | $-1,710.9568 | $336.2244 | 33.33% |

## Market Regime Attribution

| regime | trades | closed | pnl | win rate |
|---|---|---|---|---|
| RANGING | 2 | 2 | $0.0070 | 50.00% |
| TRENDING_BULL | 17 | 17 | $-678.7603 | 41.18% |
| TRENDING_BEAR | 4 | 4 | $-1,032.1966 | 0.00% |

## Execution

- filled volume: $0.0000
- total fees: $343.1845

## Risk

- max drawdown: —
- equity snapshots: 0
- replay equity: start $10,000.0000 → final $8,320.9201 · return -16.79% · maxDD 24.94% · sharpe-like -0.36613951747003975

## AI Decision Quality

- reviews: 0 ({})

## Comparison: AI Assisted vs Baseline

| metric | AI assisted | baseline |
|---|---|---|
| trades | 23 | 2 |
| total pnl | $-1,710.9499 | $8.2194 |
| win rate | 34.78% | 100.00% |
| profit factor | 0.5192699552160268 | — |
| expectancy | $-74.3891 | $4.1097 |
| avg holding | 12.8h | 167.6h |

### Replay baselines (same window)

| symbol | fixed grid | fixed trend | buy & hold |
|---|---|---|---|
| BTC | $4.2338 | $82.1107 (7 trades) | $5.2301 |
| ETH | $3.9856 | $-38.8697 (6 trades) | $1.8666 |

> Caution (§36): a higher AI PnL does not prove AI is better — consider sample size, period, market regime, fees, slippage and parameter differences before drawing conclusions.
