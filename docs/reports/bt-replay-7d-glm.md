# Experiment Report: bt-replay-7d

- experiment_id: `exp-a856ad913012`
- status: completed
- period: 2026-09-27 18:35 → 2026-09-27 20:08 (0.1 days)
- symbols: ["BTC", "ETH"]
- strategies: ["grid", "position"]
- versions: agent=0.2.0 prompt=be0c4fc3 config=7d52c911

## Market

- regimes observed (from decision events): BREAKOUT×6, RANGING×111, TRENDING_BEAR×12, TRENDING_BULL×28, UNCERTAIN×5

## Agent

- decisions: 164 ({"LONG": 6, "SCAN": 2, "WAIT": 35, "WATCH": 121})
- WAIT ratio: 21.34% · REJECT ratio: 0.00%
- trades executed: 16 of 16

## Strategy

| strategy | trades | closed | pnl | fees | win rate |
|---|---|---|---|---|---|
| position_executor | 4 | 4 | $3.3584 | $0.0000 | 50.00% |
| grid_executor | 12 | 12 | $-4.9991 | $5.2114 | 58.33% |

## Market Regime Attribution

| regime | trades | closed | pnl | win rate |
|---|---|---|---|---|
| BREAKOUT | 1 | 1 | $3.8384 | 100.00% |
| TRENDING_BULL | 3 | 3 | $-0.4800 | 33.33% |
| RANGING | 12 | 12 | $-4.9991 | 58.33% |

## Execution

- filled volume: $0.0000
- total fees: $5.2114

## Risk

- max drawdown: 0.01%
- equity snapshots: 36

## AI Decision Quality

- reviews: 0 ({})

## Comparison: AI Assisted vs Baseline

| metric | AI assisted | baseline |
|---|---|---|
| trades | 16 | 0 |
| total pnl | $-1.6407 | $0.0000 |
| win rate | 56.25% | — |
| profit factor | 0.865154226832415 | — |
| expectancy | $-0.1025 | — |
| avg holding | 15.5h | — |

> Caution (§36): a higher AI PnL does not prove AI is better — consider sample size, period, market regime, fees, slippage and parameter differences before drawing conclusions.
