# Experiment Report: bt-replay-7d

- experiment_id: `exp-3ac0a22f3e65`
- status: completed
- replayed window: 2026-10-01 09:47 → 2026-10-08 09:47 (7.0 days)
- executed (wall clock): 2026-10-08 09:47 → 2026-10-08 11:06
- symbols: ["BTC", "ETH"]
- strategies: ["grid", "position"]
- versions: agent=0.2.0 prompt=7157261b config=14838b6f

## Market

- regimes observed (from decision events): BREAKOUT×1, RANGING×173, REGIME_TRANSITION×5, TRENDING_BEAR×184, TRENDING_BULL×293, UNCERTAIN×10

## Agent

- decisions: 669 ({"CLOSE SHORT": 1, "CLOSE_LONG": 1, "CLOSE_SHORT": 2, "COVER_SHORT": 1, "HOLD": 21, "HOLD SHORT": 1, "HOLD_LONG": 7, "HOLD_SHORT": 1, "LONG": 38, "SCAN": 3, "SHORT": 30, "WAIT": 93, "WATCH": 470})
- WAIT ratio: 13.90% · REJECT ratio: 0.00%
- trades executed: 25 of 25

## Strategy

| strategy | trades | closed | pnl | fees | win rate |
|---|---|---|---|---|---|
| position_executor | 17 | 17 | $252.0401 | $135.7793 | 58.82% |
| grid_executor | 6 | 6 | $0.0541 | $0.0867 | 50.00% |

## Market Regime Attribution

| regime | trades | closed | pnl | win rate |
|---|---|---|---|---|
| TRENDING_BEAR | 6 | 6 | $186.4926 | 66.67% |
| TRENDING_BULL | 11 | 11 | $65.5475 | 54.55% |
| RANGING | 6 | 6 | $0.0541 | 50.00% |

## Execution

- filled volume: $0.0000
- total fees: $140.4304

## Risk

- max drawdown: —
- equity snapshots: 0
- replay equity: start $10,000.0000 → final $10,267.9465 · return 2.68% · maxDD 5.48% · sharpe-like 0.18658260384112965

## AI Decision Quality

- reviews: 0 ({})

## Comparison: AI Assisted vs Baseline

| metric | AI assisted | baseline |
|---|---|---|
| trades | 23 | 2 |
| total pnl | $252.0941 | $-9.5858 |
| win rate | 56.52% | 0.00% |
| profit factor | 1.2921109741760097 | — |
| expectancy | $10.9606 | $-4.7929 |
| avg holding | 10.0h | 167.7h |

### Replay baselines (same window)

| symbol | fixed grid | fixed trend | buy & hold |
|---|---|---|---|
| BTC | $-1.3293 | $-142.3216 (5 trades) | $-0.7614 |
| ETH | $-8.2565 | $-89.9702 (4 trades) | $-8.1350 |

> Caution (§36): a higher AI PnL does not prove AI is better — consider sample size, period, market regime, fees, slippage and parameter differences before drawing conclusions.
