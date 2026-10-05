# Experiment Report: bt-replay-7d

- experiment_id: `exp-66c504e488ed`
- status: completed
- replayed window: 2026-09-28 18:58 → 2026-10-05 18:58 (7.0 days)
- executed (wall clock): 2026-10-05 18:59 → 2026-10-05 19:48
- symbols: ["BTC", "ETH"]
- strategies: ["grid", "position"]
- versions: agent=0.2.0 prompt=2a37d77c config=275ffbce

## Market

- regimes observed (from decision events): BREAKOUT×5, RANGING×384, TRENDING_BEAR×38, TRENDING_BULL×240

## Agent

- decisions: 667 ({"HOLD": 38, "HOLD_SHORT": 1, "LONG": 93, "SHORT": 2, "WAIT": 56, "WATCH": 477})
- WAIT ratio: 8.40% · REJECT ratio: 0.00%
- trades executed: 44 of 44

## Strategy

| strategy | trades | closed | pnl | fees | win rate |
|---|---|---|---|---|---|
| position_executor | 18 | 18 | $9.3143 | $288.2790 | 44.44% |
| grid_executor | 24 | 24 | $-0.4355 | $0.5069 | 41.67% |

## Market Regime Attribution

| regime | trades | closed | pnl | win rate |
|---|---|---|---|---|
| TRENDING_BULL | 17 | 17 | $225.3943 | 47.06% |
| RANGING | 24 | 24 | $-0.4355 | 41.67% |
| TRENDING_BEAR | 1 | 1 | $-216.0800 | 0.00% |

## Execution

- filled volume: $0.0000
- total fees: $295.9530

## Risk

- max drawdown: —
- equity snapshots: 0
- replay equity: start $10,000.0000 → final $10,024.9191 · return 0.25% · maxDD 12.27% · sharpe-like 0.00665326381753153

## AI Decision Quality

- reviews: 0 ({})

## Comparison: AI Assisted vs Baseline

| metric | AI assisted | baseline |
|---|---|---|
| trades | 42 | 2 |
| total pnl | $8.8788 | $10.1452 |
| win rate | 42.86% | 100.00% |
| profit factor | 1.0041103640802722 | — |
| expectancy | $0.2114 | $5.0726 |
| avg holding | 5.1h | 167.5h |

### Replay baselines (same window)

| symbol | fixed grid | fixed trend | buy & hold |
|---|---|---|---|
| BTC | $4.2172 | $-80.5110 (8 trades) | $7.4506 |
| ETH | $5.9280 | $144.2114 (5 trades) | $3.9569 |

> Caution (§36): a higher AI PnL does not prove AI is better — consider sample size, period, market regime, fees, slippage and parameter differences before drawing conclusions.
