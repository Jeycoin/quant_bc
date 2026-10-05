# Experiment Report: bt-replay-7d

- experiment_id: `exp-5a5bc85adf5b`
- status: completed
- replayed window: 2026-09-28 21:43 → 2026-10-05 21:43 (7.0 days)
- executed (wall clock): 2026-10-05 21:43 → 2026-10-05 23:20
- symbols: ["BTC", "ETH"]
- strategies: ["grid", "position"]
- versions: agent=0.2.0 prompt=7b03c9c7 config=275ffbce

## Market

- regimes observed (from decision events): BREAKOUT×1, RANGING×273, TRENDING_BEAR×66, TRENDING_BULL×313, UNCERTAIN×9

## Agent

- decisions: 663 ({"COVER_SHORT": 1, "HOLD": 19, "HOLD_LONG": 6, "HOLD_SHORT": 2, "LONG": 106, "SCAN": 1, "SHORT": 16, "WAIT": 40, "WATCH": 472})
- WAIT ratio: 6.03% · REJECT ratio: 0.00%
- trades executed: 18 of 18

## Strategy

| strategy | trades | closed | pnl | fees | win rate |
|---|---|---|---|---|---|
| grid_executor | 1 | 1 | $0.0000 | $0.0000 | 0.00% |
| position_executor | 15 | 15 | $-424.9236 | $240.0470 | 40.00% |

## Market Regime Attribution

| regime | trades | closed | pnl | win rate |
|---|---|---|---|---|
| RANGING | 1 | 1 | $0.0000 | 0.00% |
| TRENDING_BEAR | 2 | 2 | $-183.2123 | 50.00% |
| TRENDING_BULL | 13 | 13 | $-241.7113 | 38.46% |

## Execution

- filled volume: $0.0000
- total fees: $246.8804

## Risk

- max drawdown: —
- equity snapshots: 0
- replay equity: start $10,000.0000 → final $9,591.0348 · return -4.09% · maxDD 16.13% · sharpe-like -0.09042498475683132

## AI Decision Quality

- reviews: 0 ({})

## Comparison: AI Assisted vs Baseline

| metric | AI assisted | baseline |
|---|---|---|
| trades | 16 | 2 |
| total pnl | $-424.9236 | $9.1224 |
| win rate | 37.50% | 100.00% |
| profit factor | 0.6312523329709114 | — |
| expectancy | $-26.5577 | $4.5612 |
| avg holding | 17.6h | 167.8h |

### Replay baselines (same window)

| symbol | fixed grid | fixed trend | buy & hold |
|---|---|---|---|
| BTC | $4.1972 | $249.5473 (5 trades) | $6.3923 |
| ETH | $4.9252 | $-48.5631 (4 trades) | $3.0467 |

> Caution (§36): a higher AI PnL does not prove AI is better — consider sample size, period, market regime, fees, slippage and parameter differences before drawing conclusions.
