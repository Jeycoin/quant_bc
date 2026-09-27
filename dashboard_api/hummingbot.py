"""Read-only Hummingbot API access for the dashboard.

Whitelist approach: only the endpoint pairs in READ_ENDPOINTS can be called.
The dashboard visualizes the existing system — it never places or cancels
orders, never creates executors, never mutates Hummingbot state. POST entries
below are search/query endpoints (Hummingbot uses POST for filtered reads).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

READ_ENDPOINTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/"),
        ("GET", "/accounts/"),
        ("GET", "/bot-orchestration/status"),
        ("GET", "/executors/summary"),
        ("GET", "/executors/{executor_id}"),
        ("POST", "/executors/search"),
        ("GET", "/performance/history"),
        ("POST", "/portfolio/state"),
        ("POST", "/portfolio/history"),
        ("POST", "/trading/positions"),
        ("POST", "/trading/orders/search"),
        ("POST", "/trading/orders/active"),
        ("POST", "/trading/trades"),
        ("POST", "/market-data/prices"),
        ("POST", "/market-data/candles"),
        ("POST", "/market-data/funding-info"),
        ("POST", "/market-data/order-book"),
    }
)


class EndpointNotAllowed(PermissionError):
    """Raised when code tries to call a non-whitelisted endpoint."""


class ReadOnlyHummingbot:
    def __init__(self) -> None:
        self.base = os.getenv("HUMMINGBOT_API_URL", "http://127.0.0.1:8100").rstrip("/")
        self._auth = (
            os.getenv("HUMMINGBOT_USERNAME", "admin"),
            os.getenv("HUMMINGBOT_PASSWORD", "admin"),
        )

    async def call(self, method: str, path: str, **kwargs: Any) -> Any:
        if (method, path) not in READ_ENDPOINTS:
            raise EndpointNotAllowed(
                f"{method} {path} is not in the dashboard read whitelist"
            )
        url = path.format(**kwargs.pop("path_params", {}))
        async with httpx.AsyncClient(
            base_url=self.base, auth=self._auth, timeout=25.0
        ) as client:
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
