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
