# Experiment Report: 7d-testnet-v1

- experiment_id: `exp-d713167f1466`
- status: running
- period: 2026-09-27 17:51 → 2026-10-03 03:41 (5.4 days)
- symbols: ["BTC", "ETH"]
- strategies: ["grid", "momentum", "mean_reversion", "breakout"]
- versions: agent=0.2.0 prompt=5f755592 config=7d52c911

## Market

- regimes observed (from decision events): BREAKOUT×1, RANGING×17, REGIME_TRANSITION×6, TRENDING_BEAR×3, TRENDING_BULL×5, UNCERTAIN×1

## Agent

- decisions: 38 ({"PROPOSE_POSITION": 3, "SCAN": 2, "WAIT": 21, "WATCH": 12})
- WAIT ratio: 55.26% · REJECT ratio: 0.00%
- trades executed: 0 of 0

## Strategy

| strategy | trades | closed | pnl | fees | win rate |
|---|---|---|---|---|---|

## Market Regime Attribution

| regime | trades | closed | pnl | win rate |
|---|---|---|---|---|

## Execution

- filled volume: $0.0000
- total fees: $0.0000

## Risk

- max drawdown: 0.19%
- equity snapshots: 204

## AI Decision Quality

- reviews: 0 ({})

## Comparison: AI Assisted vs Baseline

| metric | AI assisted | baseline |
|---|---|---|
| trades | 0 | 0 |
| total pnl | $0.0000 | $0.0000 |
| win rate | — | — |
| profit factor | — | — |
| expectancy | — | — |
| avg holding | — | — |

> Caution (§36): a higher AI PnL does not prove AI is better — consider sample size, period, market regime, fees, slippage and parameter differences before drawing conclusions.
