# Experiment Report: bt-replay-90d

> **Superseded 2026-10-04**: this experiment was resumed via `--resume-exp` and
> completed at 91.0 days. Final report: [bt-replay-90d-deepseek.md](bt-replay-90d-deepseek.md).
> This file is kept as the historical record of the interrupted run.

- experiment_id: `exp-23f2df348a8e`
- status: failed
- replayed window: 2026-07-05 23:00 → 2026-09-30 20:00 (86.9 days)
- executed (wall clock): 2026-10-03 15:47 → 2026-10-03 18:40
- symbols: ["BTC", "ETH"]
- strategies: ["grid", "position"]
- versions: agent=0.2.0 prompt=ea2abb0b config=444bc21c

## Market

- regimes observed (from decision events): BREAKOUT×75, HIGH_VOLATILITY×10, LOW_VOLATILITY×49, RANGING×1398, REGIME_TRANSITION×90, TRENDING_BEAR×194, TRENDING_BULL×188, UNCERTAIN×13

## Agent

- decisions: 2057 ({"LONG": 119, "SCAN": 40, "SHORT": 23, "WAIT": 550, "WATCH": 1325})
- WAIT ratio: 26.74% · REJECT ratio: 0.00%
- trades executed: 174 of 174

## Strategy

| strategy | trades | closed | pnl | fees | win rate |
|---|---|---|---|---|---|
| position_executor | 93 | 93 | $5.7260 | $14.8991 | 40.86% |
| grid_executor | 81 | 81 | $-15.8777 | $27.0635 | 46.91% |

## Market Regime Attribution

| regime | trades | closed | pnl | win rate |
|---|---|---|---|---|
| BREAKOUT | 37 | 37 | $34.0539 | 54.05% |
| REGIME_TRANSITION | 3 | 3 | $-0.0297 | 66.67% |
| RANGING | 82 | 82 | $-12.0393 | 47.56% |
| TRENDING_BULL | 38 | 38 | $-14.3297 | 34.21% |
| TRENDING_BEAR | 14 | 14 | $-17.8070 | 14.29% |

## Execution

- filled volume: $0.0000
- total fees: $41.9626

## Risk

- max drawdown: —
- equity snapshots: 0
- replay equity: start $10,000.0000 → final $9,989.8483 · return -0.10% · maxDD 0.42% · sharpe-like -0.03729002800730267

## AI Decision Quality

- reviews: 0 ({})

## Comparison: AI Assisted vs Baseline

| metric | AI assisted | baseline |
|---|---|---|
| trades | 174 | 0 |
| total pnl | $-10.1517 | $0.0000 |
| win rate | 43.68% | — |
| profit factor | 0.9371144076171042 | — |
| expectancy | $-0.0583 | — |
| avg holding | 12.9h | — |

### Replay baselines (same window)

| symbol | fixed grid | fixed trend | buy & hold |
|---|---|---|---|
| BTC | $16.0893 | $-18.3173 (90 trades) | $69.8015 |
| ETH | $7.1352 | $-30.6449 (127 trades) | $103.4205 |

> Caution (§36): a higher AI PnL does not prove AI is better — consider sample size, period, market regime, fees, slippage and parameter differences before drawing conclusions.
