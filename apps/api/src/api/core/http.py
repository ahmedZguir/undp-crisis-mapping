"""Outbound HTTP helper for the channel adapters."""

from __future__ import annotations

from typing import Any

import httpx


async def http_request(
    client: httpx.AsyncClient | None,
    method: str,
    url: str,
    *,
    timeout: float,
    **kwargs: Any,
) -> httpx.Response:
    # ``timeout`` applies only to the throwaway client; an injected one keeps its own.
    if client is not None:
        return await client.request(method, url, **kwargs)
    async with httpx.AsyncClient(timeout=timeout) as c:
        return await c.request(method, url, **kwargs)
