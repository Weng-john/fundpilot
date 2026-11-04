"""Minimal JSON-RPC client for read-only ``eth_call`` against any EVM chain."""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from typing import Any

import httpx


class RpcError(RuntimeError):
    pass


class JsonRpcClient:
    def __init__(
        self,
        url: str,
        *,
        timeout: float = 15.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._url = url
        self._ids = itertools.count(1)
        self._retries = max_retries
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, params: list[Any]) -> Any:
        body = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._client.post(self._url, json=body)
            except httpx.HTTPError as exc:
                last = RpcError(f"transport error: {exc}")
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = RpcError(f"HTTP {resp.status_code}")
                else:
                    data = resp.json()
                    if "error" in data:
                        raise RpcError(f"{method} failed: {data['error']}")
                    return data["result"]
            if attempt < self._retries:
                self._sleep(2**attempt)
        raise last or RpcError("request failed")

    def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        result: str = self.request("eth_call", [{"to": to, "data": data}, block])
        return result

    def block_number(self) -> int:
        return int(self.request("eth_blockNumber", []), 16)
