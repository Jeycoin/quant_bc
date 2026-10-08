"""External data providers (v0.4 Phase 4).

Rules (master prompt):
- Prefer mature APIs / MCP over scraping. No scrapers here — only public
  REST APIs.
- Providers are pluggable and fail independently: one source dying must
  never affect the others, and never affect trading.
- Key-gated providers (LunarCrush, Glassnode) are disabled unless their
  env key is set — the collector reports them as unavailable, not broken.

Currently wired:
- RSS news (CoinTelegraph, CoinDesk — no key): categorized + severity-scored
- alternative.me Fear & Greed (no key): market-wide sentiment
- blockchain.info (no key): BTC network activity
- LunarCrush (key: LUNARCRUSH_API_KEY): social volume/sentiment/dominance
- Glassnode (key: GLASSNODE_API_KEY): on-chain exchange flows, active addresses
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx

from intelligence.schema import IntelSignal, NewsEvent

_TIMEOUT = 15.0
_USER_AGENT = {"User-Agent": "ai-quant-intel/0.4"}

# asset keyword -> canonical symbol
_ASSET_KEYWORDS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth", "ether"],
    "SOL": ["solana", "sol"],
}

_CATEGORY_KEYWORDS = {
    "etf": ["etf"],
    "regulation": ["sec", "regulat", "ban", "lawsuit", "court", "cftc", "miCA".lower()],
    "hack": ["hack", "exploit", "breach", "stolen", "drain"],
    "listing": ["listing", "lists ", "listed"],
    "delisting": ["delist"],
    "protocol_upgrade": ["upgrade", "hard fork", "hardfork", "mainnet", "eip-"],
    "partnership": ["partner", "collaborat", "integrat"],
    "macro": ["fed", "inflation", "cpi", "interest rate", "fomc", "treasury"],
    "exchange": ["binance", "coinbase", "kraken", "okx", "bybit"],
}

_SEVERITY_KEYWORDS = {
    0.9: ["hack", "exploit", "stolen", "bankrupt", "collapse", "ban", "sec sues"],
    0.75: ["etf approv", "etf reject", "etf launch", "delist", "emergency"],
    0.6: ["listing", "upgrade", "mainnet", "record high", "all-time high"],
}


def detect_asset(text: str) -> str | None:
    low = text.lower()
    for symbol, keywords in _ASSET_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(k)}\b", low) for k in keywords):
            return symbol
    return None


def categorize(text: str) -> str:
    low = text.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(k in low for k in keywords):
            return category
    return "general"


def score_severity(text: str) -> float:
    low = text.lower()
    for severity, keywords in sorted(_SEVERITY_KEYWORDS.items(), reverse=True):
        if any(k in low for k in keywords):
            return severity
    return 0.4


class Provider:
    name = "base"

    def available(self) -> bool:
        return True

    async def fetch(self, symbols: list[str]) -> dict[str, Any]:
        """Returns {"signals": [IntelSignal], "news": [NewsEvent]}."""
        raise NotImplementedError


class RssNews(Provider):
    """Crypto news from public RSS feeds (CoinTelegraph, CoinDesk).

    No API key, no scraping of HTML pages — RSS is the providers' official
    syndication format. Only title/summary are kept; full article text is
    never fetched or stored.
    """
    name = "rss_news"

    FEEDS = [
        ("cointelegraph", "https://cointelegraph.com/rss"),
        ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ]

    async def fetch(self, symbols: list[str]) -> dict[str, Any]:
        import xml.etree.ElementTree as ET

        news: list[NewsEvent] = []
        async with httpx.AsyncClient(timeout=_TIMEOUT,
                                     headers=_USER_AGENT,
                                     follow_redirects=True) as client:
            for feed_name, url in self.FEEDS:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except Exception:
                    continue
                try:
                    root = ET.fromstring(resp.text)
                except ET.ParseError:
                    continue
                for item in root.iter("item"):
                    title = (item.findtext("title") or "").strip()
                    if not title:
                        continue
                    summary = re.sub(r"<[^>]+>", " ",
                                     item.findtext("description") or "")
                    summary = " ".join(summary.split())[:300]
                    pub = item.findtext("pubDate") or ""
                    try:
                        from email.utils import parsedate_to_datetime
                        ts = parsedate_to_datetime(pub).timestamp()
                    except (TypeError, ValueError):
                        ts = time.time()
                    text = f"{title} {summary}"
                    asset = detect_asset(text)
                    news.append(NewsEvent(
                        title=title[:200],
                        source=f"rss/{feed_name}",
                        summary=summary,
                        asset=asset,
                        category=categorize(text),
                        severity=score_severity(text),
                        relevance=0.8 if asset else 0.4,
                        url=(item.findtext("link") or "").strip(),
                        ts=ts,
                    ))
        return {"signals": [], "news": news[:60]}


class FearGreed(Provider):
    name = "alternative_me_fng"

    async def fetch(self, symbols: list[str]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get("https://api.alternative.me/fng/",
                                    params={"limit": 2})
            resp.raise_for_status()
            data = resp.json().get("data", [])
        if not data:
            return {"signals": [], "news": []}
        value = float(data[0]["value"])
        signals = [IntelSignal(
            kind="fear_greed", symbol=None, source=self.name, value=value,
            confidence=0.7, relevance=1.0,
            payload={"classification": data[0].get("value_classification")},
            ts=float(data[0].get("timestamp", time.time())),
        )]
        if len(data) > 1:
            prev = float(data[1]["value"])
            signals.append(IntelSignal(
                kind="sentiment_change", symbol=None, source=self.name,
                value=value - prev, confidence=0.7, relevance=1.0,
                payload={"window": "1d", "from": prev, "to": value},
                ts=float(data[0].get("timestamp", time.time())),
            ))
        return {"signals": signals, "news": []}


class BlockchainInfo(Provider):
    """BTC network activity (no key). Maps loosely onto network_activity;
    exchange-flow / whale data needs key-gated providers."""
    name = "blockchain_info"

    async def fetch(self, symbols: list[str]) -> dict[str, Any]:
        if "BTC" not in symbols:
            return {"signals": [], "news": []}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                "https://api.blockchain.info/charts/n-transactions",
                params={"timespan": "7days", "format": "json"})
            resp.raise_for_status()
            values = resp.json().get("values", [])
        if len(values) < 2:
            return {"signals": [], "news": []}
        latest, prev = values[-1], values[-2]
        change_pct = (latest["y"] - prev["y"]) / prev["y"] if prev["y"] else 0.0
        return {"signals": [IntelSignal(
            kind="network_activity", symbol="BTC", source=self.name,
            value=float(latest["y"]), confidence=0.8, relevance=0.7,
            payload={"metric": "n_transactions",
                     "change_pct_1d": round(change_pct * 100, 2)},
            ts=float(latest["x"]),
        )], "news": []}


class LunarCrush(Provider):
    """Social volume / sentiment / dominance. Disabled without API key."""
    name = "lunarcrush"

    def available(self) -> bool:
        return bool(os.getenv("LUNARCRUSH_API_KEY"))

    async def fetch(self, symbols: list[str]) -> dict[str, Any]:
        key = os.getenv("LUNARCRUSH_API_KEY")
        signals = []
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for symbol in symbols:
                resp = await client.get(
                    f"https://lunarcrush.com/api4/public/coins/{symbol.lower()}/v1",
                    headers={"Authorization": f"Bearer {key}"})
                if resp.status_code != 200:
                    continue
                d = resp.json().get("data", {})
                ts = time.time()
                for kind, key_ in [("social_volume", "interactions_24h"),
                                   ("sentiment", "sentiment"),
                                   ("social_dominance", "social_dominance"),
                                   ("creator_activity", "social_contributors_created_24h")]:
                    if d.get(key_) is not None:
                        signals.append(IntelSignal(
                            kind=kind, symbol=symbol, source=self.name,
                            value=float(d[key_]), confidence=0.8, relevance=0.9,
                            ts=ts))
        return {"signals": signals, "news": []}


class Glassnode(Provider):
    """On-chain exchange flows + active addresses. Disabled without key."""
    name = "glassnode"

    def available(self) -> bool:
        return bool(os.getenv("GLASSNODE_API_KEY"))

    async def fetch(self, symbols: list[str]) -> dict[str, Any]:
        key = os.getenv("GLASSNODE_API_KEY")
        signals = []
        metrics = [("exchange_inflow", "transactions/transfers_volume_to_exchanges_sum"),
                   ("exchange_outflow", "transactions/transfers_volume_from_exchanges_sum"),
                   ("active_addresses", "addresses/active_count")]
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for symbol in symbols:
                if symbol not in ("BTC", "ETH"):
                    continue
                for kind, metric in metrics:
                    resp = await client.get(
                        f"https://api.glassnode.com/v1/metrics/{metric}",
                        params={"a": symbol, "api_key": key, "i": "24h"})
                    if resp.status_code != 200:
                        continue
                    points = resp.json()
                    if points:
                        signals.append(IntelSignal(
                            kind=kind, symbol=symbol, source=self.name,
                            value=float(points[-1]["v"]), confidence=0.85,
                            relevance=0.85,
                            ts=float(points[-1]["t"]),
                        ))
        return {"signals": signals, "news": []}


class HyperliquidDeriv(Provider):
    """Derivatives positioning intelligence from the public Hyperliquid info
    endpoint (metaAndAssetCtxs): open interest (USD notional) and current
    funding rate per coin. No API key. This is the venue the deployment
    already uses for market data, so coverage matches the watchlist exactly.

    Why it matters (frontier notes §13): price + OI divergence separates new
    money from short squeezes (price up + OI up = new longs; price up + OI
    down = shorts covering), and cross-sectional funding ranks crowding.
    """
    name = "hyperliquid_deriv"

    async def fetch(self, symbols: list[str]) -> dict[str, Any]:
        signals = []
        proxy = os.environ.get("MARKET_DATA_PROXY") or None
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False,
                                     proxy=proxy) as client:
            resp = await client.post("https://api.hyperliquid.xyz/info",
                                     json={"type": "metaAndAssetCtxs"})
            resp.raise_for_status()
            meta, ctxs = resp.json()
        names = [u.get("name") for u in meta.get("universe", [])]
        now = time.time()
        for coin, ctx in zip(names, ctxs):
            if coin not in symbols:
                continue
            try:
                mark = float(ctx.get("markPx", 0) or 0)
                oi_usd = float(ctx.get("openInterest", 0) or 0) * mark
                funding = float(ctx.get("funding", 0) or 0)
            except (TypeError, ValueError):
                continue
            signals.append(IntelSignal(
                kind="open_interest", symbol=coin, source=self.name,
                value=oi_usd, confidence=0.9, relevance=0.9, ts=now))
            signals.append(IntelSignal(
                kind="funding_rate", symbol=coin, source=self.name,
                value=funding, confidence=0.9, relevance=0.9, ts=now))
        return {"signals": signals, "news": []}


ALL_PROVIDERS: list[Provider] = [
    RssNews(),
    FearGreed(),
    BlockchainInfo(),
    LunarCrush(),
    Glassnode(),
    HyperliquidDeriv(),
]
