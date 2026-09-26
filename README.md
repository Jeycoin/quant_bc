# AI Quant Trading Agent

An LLM-driven trading manager that orchestrates **Hummingbot** through its
official **MCP server**. Hummingbot provides exchange connectors, market
data, strategies (V2 controllers/executors), order and position management.
This repo contains only the thin layer that is not already provided:

```
LLM  →  agent/ (this repo)  →  hummingbot-mcp  →  Hummingbot API  →  Exchange
```

## What this repo owns

- `agent/` — agent loop, prompts, safety boundary, memory (SQLite)
- `config/settings.yaml` — hard safety limits (paper connectors, symbol
  whitelist, order size caps, blocked actions)
- `scripts/verify_paper.py` — paper-trading path verification

Everything else (connectors, market data, strategies, execution, orders,
positions) comes from Hummingbot. Do not reimplement it here.

## Safety model

- **Default is PAPER.** Real trading requires `LIVE_TRADING=true` in `.env`,
  set by the user — never by the agent.
- In PAPER mode the safety guard rejects any executor on a non-paper/testnet
  connector.
- The agent cannot delete credentials, widen its own limits, or bypass the
  guard — `agent/safety.py` sits between the LLM and every MCP tool call.

## Setup

1. Install Docker (Docker Engine in WSL2 works), then deploy the Hummingbot API:
   ```bash
   # inside WSL2 / Linux
   curl -fsSL https://raw.githubusercontent.com/hummingbot/deploy/main/setup.sh | bash -s -- --hummingbot-api
   cd hummingbot-api && make deploy
   ```
2. `cp .env.example .env` and fill in an LLM key (any provider —
   Claude, GLM, DashScope, DeepSeek, Kimi).
3. `pip install -e .`
4. Verify the stack: `python scripts/verify_paper.py` and `python scripts/test_mcp.py`
5. Run the agent: `python -m agent.agent` (interactive) or
   `python scripts/e2e_mvp.py "your request"` (single task)
6. Run tests: `python -m pytest tests/`

See **USAGE.md** (中文) for the full operating manual, current deployment
facts (API port, proxy, WSL idle-timeout fix) and troubleshooting.

## Modes

- **Research mode**: read-only analysis and reviews (ask the agent to
  review past decisions; reviews never change strategy parameters).
- **Paper mode** (MVP default): real market data from live connectors,
  simulated execution on exchange **testnet** connectors. (Verified: the
  API does not expose `*_paper_trade` connectors — see
  `docs/research-notes.md`.)
- **Live mode**: only with explicit `LIVE_TRADING=true`.

## Known limitations

- V2 controllers do not support paper_trade connectors (documented by
  Hummingbot) — MVP strategies run as V2 scripts or on exchange testnets.
  See `docs/research-notes.md` for the full capability assessment.
