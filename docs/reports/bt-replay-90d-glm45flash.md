# Experiment Report: bt-replay-90d

- experiment_id: `exp-eb401d316df2`
- status: completed
- replayed window: 2026-07-05 03:41 → 2026-10-03 03:41 (90.0 days)
- executed (wall clock): 2026-10-03 03:41 → 2026-10-03 17:57
- symbols: ["BTC", "ETH"]
- strategies: ["grid", "position"]
- versions: agent=0.2.0 prompt=ea2abb0b config=444bc21c

## Market

- regimes observed (from decision events): BREAKOUT×3, LOW_VOLATILITY×2, RANGING×1785, TRENDING_BEAR×78, TRENDING_BULL×287

## Agent

- decisions: 2155 ({"HOLD": 37, "HOLD_GRID": 1, "LONG": 17, "MONITOR": 1, "WAIT": 125, "WATCH": 1974})
- WAIT ratio: 5.80% · REJECT ratio: 0.00%
- trades executed: 82 of 82

## Strategy

| strategy | trades | closed | pnl | fees | win rate |
|---|---|---|---|---|---|
| position_executor | 12 | 12 | $-7.6092 | $1.9177 | 25.00% |
| grid_executor | 68 | 68 | $-18.1499 | $30.6143 | 61.76% |

## Market Regime Attribution

| regime | trades | closed | pnl | win rate |
|---|---|---|---|---|
| TRENDING_BULL | 12 | 12 | $-7.6092 | 25.00% |
| RANGING | 68 | 68 | $-18.1499 | 61.76% |

## Execution

- filled volume: $0.0000
- total fees: $49.5090

## Risk

- max drawdown: —
- equity snapshots: 0
- replay equity: start $10,000.0000 → final $9,972.6214 · return -0.27% · maxDD 0.45% · sharpe-like -0.09644906614246576

## AI Decision Quality

- reviews: 0 ({})

## Comparison: AI Assisted vs Baseline

| metric | AI assisted | baseline |
|---|---|---|
| trades | 80 | 2 |
| total pnl | $-25.7591 | $25.4230 |
| win rate | 56.25% | 100.00% |
| profit factor | 0.6450174283259195 | — |
| expectancy | $-0.3220 | $12.7115 |
| avg holding | 21.5h | 2159.3h |

### Replay baselines (same window)

| symbol | fixed grid | fixed trend | buy & hold |
|---|---|---|---|
| BTC | $17.4683 | $-20.7277 (96 trades) | $66.3799 |
| ETH | $7.9547 | $-29.6579 (133 trades) | $98.4392 |

> Caution (§36): a higher AI PnL does not prove AI is better — consider sample size, period, market regime, fees, slippage and parameter differences before drawing conclusions.
