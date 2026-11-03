"""Minimal OpenAI-compatible chat-completions client (``POST {base_url}/chat/completions``).

Works with any provider exposing that API (hosted or local). Not required: without
configuration the agent runs in deterministic mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class LLMError(RuntimeError):
    pass


@dataclass(slots=True)
class OpenAICompatibleClient:
    base_url: str
    model: str
    api_key: str | None = None
    timeout: float = 60.0
    transport: httpx.BaseTransport | None = None
    temperature: float = 0.2

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            body["tools"] = tools
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            try:
                resp = client.post(
                    self.base_url.rstrip("/") + "/chat/completions", json=body, headers=headers
                )
            except httpx.HTTPError as exc:
                raise LLMError(f"LLM request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise LLMError(f"LLM returned HTTP {resp.status_code}: {resp.text[:200]}")
        data: dict[str, Any] = resp.json()
        try:
            message: dict[str, Any] = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response shape: {str(data)[:200]}") from exc
        return message
