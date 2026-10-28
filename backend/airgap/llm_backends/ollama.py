"""Ollama adapter (POST /api/chat, NDJSON stream).

API shape checked against docs.ollama.com/api/chat on 2026-10-28. The Ollama runtime version
is pinned in deploy/docker-compose.yml.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

import httpx

from airgap.core.ports import GenerationOptions
from airgap.core.types import ChatMessage


class LLMUnavailable(RuntimeError):
    """The runtime could not be reached or returned an error."""


class OllamaBackend:
    def __init__(
        self, base_url: str, model: str, *, timeout_s: float = 300.0, keep_alive: str = "30m"
    ) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=httpx.Timeout(timeout_s, connect=5))
        self._model = model
        self._keep_alive = keep_alive

    @property
    def model_id(self) -> str:
        return f"ollama:{self._model}"

    def _body(
        self, messages: Sequence[ChatMessage], options: GenerationOptions, stream: bool
    ) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "temperature": options.temperature,
            "num_predict": options.max_tokens,
        }
        if options.num_ctx:
            opts["num_ctx"] = options.num_ctx
        if options.stop:
            opts["stop"] = list(options.stop)
        return {
            "model": self._model,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            "stream": stream,
            "options": opts,
            "keep_alive": self._keep_alive,
        }

    def stream(self, messages: Sequence[ChatMessage], options: GenerationOptions) -> Iterator[str]:
        try:
            with self._client.stream(
                "POST", "/api/chat", json=self._body(messages, options, True)
            ) as resp:
                if resp.status_code != 200:
                    resp.read()
                    raise LLMUnavailable(f"ollama returned {resp.status_code}: {resp.text[:200]}")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    if "error" in event:
                        raise LLMUnavailable(f"ollama error: {event['error']}")
                    delta = event.get("message", {}).get("content", "")
                    if delta:
                        yield delta
                    if event.get("done"):
                        return
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"ollama unreachable: {type(exc).__name__}") from exc

    def complete(self, messages: Sequence[ChatMessage], options: GenerationOptions) -> str:
        try:
            resp = self._client.post("/api/chat", json=self._body(messages, options, False))
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"ollama unreachable: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise LLMUnavailable(f"ollama returned {resp.status_code}: {resp.text[:200]}")
        return str(resp.json().get("message", {}).get("content", ""))

    def healthy(self) -> bool:
        try:
            resp = self._client.get("/api/tags", timeout=3)
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            return False
        names = {m.get("name") for m in resp.json().get("models", [])}
        return self._model in names
