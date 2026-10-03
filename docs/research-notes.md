# Hummingbot Capability Assessment (2026-09)

Phase-1 research findings. Sources: hummingbot.org docs, github.com/hummingbot
(hummingbot, mcp, hummingbot-api, deploy), release notes up to v2.17.0.

## Versions

- Hummingbot **v2.17.0** (2026-09-22); monthly release cadence.
- Hummingbot API (FastAPI, port 8000): `hummingbot/hummingbot-api`, v1.0.1,
  Swagger at `/docs`. Stack: API + EMQX broker + Postgres, all Docker.
- MCP server: `hummingbot/mcp`, PyPI `hummingbot-mcp` v1.0.4, Docker image
  `hummingbot/hummingbot-mcp`. Thin stdio wrapper over the API. Maintained
  but slow-moving.
- **Condor**: official open-source trading-agent harness (LLM OODA loop +
  deterministic execution via the API, Telegram + web dashboard :8088).
  Install: `hummingbot/deploy` setup.sh. Must be evaluated before building
  more orchestration ourselves.

## MCP tools (11, verified from server.py)

configure_server, setup_connector, get_portfolio_overview,
set_account_position_mode_and_leverage, search_history, get_market_data
(prices/candles/funding/order_book), manage_controllers, manage_bots,
manage_executors (order/position/grid/dca/lp — the only way to trade;
there is no place_order tool), explore_dex_pools, explore_geckoterminal.

Gateway swap/config tools exist in source but are not registered as tools.

## Strategies (V2 controllers)

- Market making / mean reversion: pmm_simple, pmm_dynamic, dman_maker_v2,
  pmm_mister, pmm_v1
- Directional: bollinger_v1/v2, bollingrid, macd_bb_v1 (momentum),
  supertrend_v1 (trend), dman_v3, ai_livestream (external signal)
- Grid: grid_strike, multi_grid_strike, quantum_grid_allocator
- Arb: arbitrage_controller, xemm_multiple_levels, stat_arb
- Executors: position, dca, grid, twap, arbitrage, xemm, lp, order

## Connectors

Binance (spot+perp), OKX (spot+perp), Bybit (spot+perp), Hyperliquid
(spot+perp, own testnet) all actively maintained. 30+ others.

## Paper trading — VERIFIED (2026-09-27, hands-on)

- API exposes **no** `*_paper_trade` connectors (confirmed against live API).
- Paper path = **testnet connectors** (15 available: hyperliquid_testnet,
  hyperliquid_perpetual_testnet, binance_perpetual_testnet, bybit_testnet, ...).
- market-data endpoints only support LIVE connectors: agent reads market
  data from live connectors and executes on testnet connectors.
- Network constraint (this deployment, CN network): binance/okx/bybit/kucoin
  APIs unreachable from containers (aiohttp ignores HTTP_PROXY env);
  **hyperliquid + gate_io are directly reachable** → Hyperliquid is the MVP
  venue: market data `hyperliquid` / `hyperliquid_perpetual` (live, read),
  execution `hyperliquid_testnet` / `hyperliquid_perpetual_testnet` (paper).
- Pair naming: spot `UBTC-USDC` (305 pairs), perp `BTC-USD` (527 pairs).
- MCP smoke test passed: 11 tools listed, live price fetched end-to-end.
- hummingbot-mcp 1.0.4 needs mcp SDK <2 → run via
  `uvx --from hummingbot-mcp --with "mcp>=1.0,<2" hummingbot-mcp`.

## Deployment notes (this machine)

- Hummingbot API on port **8100** (8000 taken by a local godot_ai service),
  containers in WSL2 (~/hummingbot-api), proxy env for container egress.
- `.wslconfig` must keep `instanceIdleTimeout=-1` / `vmIdleTimeout=-1` or
  Windows kills the idle WSL VM (was reboot-looping every 1-3 min).
- Clash "Allow LAN" enabled; container proxy = http://192.168.192.1:7890.

## Not provided by Hummingbot (potential external data, phase 2+)

Open interest, liquidations, on-chain data → CoinGlass / CryptoQuant etc.
Only add when the agent actually needs them.

## What we build ourselves

Agent prompts/workflows, safety boundary (agent/safety.py — the MCP layer
has no paper mode or spend limits), decision/review memory
(agent/memory/store.py), research/review reporting.

## Grid backtest findings (2026-09-27, scripts/backtest_grid.py)

Backtester: Hyperliquid public API 1h candles, candle-range touch fills,
flat 0.04% fee per side. BTC 30d (79.5K → 84.9K, trending):

| tp/level | trades | grid PnL ($300) | buy&hold |
|---|---|---|---|
| 0.02% (live config) | 967 | **-$17.46** | +$20.29 |
| 0.1% | 177 | -$6.20 | +$20.29 |
| 0.2% | 173 | +$0.12 | +$20.29 |
| 0.5% | 67 | +$5.13 | +$20.29 |

Ranging window (5d, 83.2K–85.2K, tp=0.2%): grid **+$4.09** vs buy&hold
**-$3.63**.

Conclusions:
1. **Fee floor**: per-level take-profit must exceed ~2× the round-trip fee
   or the grid is structurally loss-making. The 0.02% live config loses on
   any real fee schedule — testnet "profit" was an artifact.
2. Grid beats buy&hold only in ranging regimes; it caps upside in trends.
   This is exactly the regime-selection value the agent is meant to add.
3. Backtest limits: 1h candle granularity, no intrabar path, no slippage
   model, flat fee — use for parameter sanity checks, not precision.

## 30d agent replay — DeepSeek-V4.1-Flash (2026-10-02, exp-ac0ec11406f7)

First full-window replay after the v0.4 cost/risk layer. 713 decision
points (BTC+ETH, every 2h), 2026-09-03 → 10-02 (29d replayed).

| | agent | fixed grid (BTC/ETH) | fixed trend | buy&hold |
|---|---|---|---|---|
| net PnL | **+$16.92** (+0.17%) | +$4.94 / +$11.03 | +$4.05 / +$5.92 | +$22.22 / +$26.22 |

- 713 decisions: WATCH 460, WAIT 191, LONG 46, SHORT 9 — the agent stays
  out 91% of the time; trading discipline works.
- Cost Validator effect vs the 7d GLM replay (net -0.02%): grid trades now
  net-positive ($3.94 net on $9.89 fees — still a 2.5x fee drag, but no
  longer structurally negative). Position executor drives PnL (+$12.98 net).
- Regime attribution: BREAKOUT +$16.37 (75% win) is the earner;
  TRENDING_BULL -$0.27 and TRENDING_BEAR -$3.12 lag — trend entries remain
  the weak spot.
- Buy&hold beat everything (+$22-26 per $200) — the window was a sustained
  uptrend. The honest read: the agent adds drawdown control (maxDD 0.23%)
  and fee discipline, not yet raw return vs passive in a bull leg.
- Caveat: DeepSeek training cutoff unknown, replayed window may be in
  training data — contamination cannot be ruled out. Forward testnet
  results (exp-d713167f1466) remain the clean evidence.
- Report: docs/reports/bt-replay-30d-deepseek.md

## WSL2 mirrored-mode incident (2026-10-02)

After a 4d shutdown, containers lost all egress (TCP handshake ok, TLS
stalled) while the WSL VM itself was fine. Root cause: .wslconfig had
`networkingMode=mirrored`, which breaks docker bridge NAT. Fix:
`networkingMode=NAT` (backup at ~/.wslconfig.bak-20261002). Hummingbot
market data recovered immediately.

## Replay summary persistence (2026-10-02)

experiments.summary_json (additive column) now stores replayed window,
agent equity metrics and all three baselines, so reports render offline
and never mix baseline trades into agent regime attribution
(analytics/queries.attribution gained a source filter; report renders the
baseline table from summary_json). scripts/backfill_replay_summary.py
rebuilds summaries for older replay experiments without LLM calls.

## DeepSeek balance outage (2026-10-03 ~03:00)

The 90d replay (exp-7881c7f72f2f) hit **402 Insufficient Balance** from
DeepSeek partway through (~decision 175 of ~2160). All later decision
points fell back to no-trade, so the run is marked `invalid` — do not
analyze it. The displayed "-0.07% / 14 trades" is the fallback artifact,
not agent behavior.

Also discovered: all local background processes froze when the machine
slept (~18:30 10-02 → 03:04 10-03); Windows kept them as zombies with
~0 CPU. Recovery = kill + restart; dashboards (uvicorn/next) survived
but both python loops and the replay needed a restart.

Fallback action: `.env` switched to GLM `glm-5.3-flash` (DeepSeek lines
preserved as comments; restore after top-up). Live loop continues on GLM
— decision events carry `llm_model`, so the model switch is auditable
per decision. The 90d replay is postponed until DeepSeek is recharged
(GLM at ~66s/decision point would need ~40h for 90d).

Lesson for ops: long replays should checkpoint — a `--resume` flag for
backtest_agent.py is now the top robustness TODO.

## 90d dual-model replay (2026-10-03)

Two 90d replays completed the sample:

- **GLM-4.5-flash (thinking off), full 90d** (exp-eb401d316df2): net -0.27%,
  82 trades, 92% WATCH. Grid -18.15 (fees 30.61). Conservative.
- **DeepSeek-flash, 86.9/90d** (exp-23f2df348a8e): net -0.10%, 174 trades,
  fees 41.96 (~57% of gross). Ran out of balance at replayed 09-30; fail-fast
  (added today) marked it failed cleanly — no fallback pollution. The missing
  3 days are covered by the 30d run.

Cross-window regime attribution is now consistent (7d/30d/90d, both models):
BREAKOUT is the only reliably profitable regime (+3.84 / +16.37 / +34.05);
TRENDING_BEAR shorts lose most (14% win over 90d); RANGING grids are
net-negative at scale due to fees. Agent beats the fixed-trend baseline
(-18/-31) but not fixed grid or buy&hold.

Live loop switched to GLM-4.5-flash with thinking disabled (8.5s/decision)
to preserve the near-zero DeepSeek balance; llm_model column tracks it.
Full write-up: docs/framework-analysis-v0.4.md
