"""Model lifecycle on an LLM runtime: list, pull (with progress), verify, unload, inference check.

``RuntimeAdmin`` is the port; ``OllamaAdmin`` the first adapter (API checked against
docs.ollama.com/api on 2026-10-28; runtime 0.40.12). Ollama resumes interrupted pulls itself
(partial blobs are kept), so a retried pull continues rather than restarting.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

PullProgress = Callable[[str, int | None, int | None], None]  # status, completed, total


class RuntimeError_(RuntimeError):  # noqa: N801 - avoid shadowing the builtin
    pass


class PullCancelled(RuntimeError_):
    pass


@dataclass(frozen=True, slots=True)
class InstalledModel:
    tag: str
    size_bytes: int
    digest: str


@dataclass(frozen=True, slots=True)
class InferenceCheck:
    ok: bool
    seconds: float
    reply: str
    error: str | None = None


class RuntimeAdmin(Protocol):
    def installed(self) -> list[InstalledModel]: ...

    def pull(
        self, tag: str, progress: PullProgress | None, cancel: threading.Event | None
    ) -> None: ...

    def unload(self, tag: str) -> None: ...

    def inference_check(self, tag: str, num_ctx: int) -> InferenceCheck: ...


class OllamaAdmin:
    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self._http = client or httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(600, connect=5)
        )

    def installed(self) -> list[InstalledModel]:
        try:
            resp = self._http.get("/api/tags", timeout=10)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError_(f"runtime unreachable: {type(exc).__name__}") from exc
        return [
            InstalledModel(m["name"], int(m.get("size", 0)), str(m.get("digest", "")))
            for m in resp.json().get("models", [])
        ]

    def pull(
        self,
        tag: str,
        progress: PullProgress | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        try:
            with self._http.stream("POST", "/api/pull", json={"model": tag, "stream": True}) as r:
                if r.status_code != 200:
                    r.read()
                    raise RuntimeError_(f"pull failed: HTTP {r.status_code}")
                for line in r.iter_lines():
                    if cancel is not None and cancel.is_set():
                        raise PullCancelled("pull cancelled; the runtime keeps partial data")
                    if not line:
                        continue
                    event = json.loads(line)
                    if "error" in event:
                        raise RuntimeError_(f"pull failed: {event['error']}")
                    if progress:
                        progress(
                            str(event.get("status", "")), event.get("completed"), event.get("total")
                        )
                    if event.get("status") == "success":
                        return
        except httpx.HTTPError as exc:
            raise RuntimeError_(f"runtime unreachable: {type(exc).__name__}") from exc
        raise RuntimeError_("pull ended without success")

    def unload(self, tag: str) -> None:
        try:
            self._http.post("/api/generate", json={"model": tag, "keep_alive": 0}, timeout=30)
        except httpx.HTTPError as exc:
            raise RuntimeError_(f"runtime unreachable: {type(exc).__name__}") from exc

    def inference_check(self, tag: str, num_ctx: int) -> InferenceCheck:
        """A REAL generation request. The only check that proves the model loads and answers."""
        body = {
            "model": tag,
            "messages": [{"role": "user", "content": "Reply with exactly one word: ready"}],
            "stream": False,
            "options": {"num_predict": 16, "temperature": 0, "num_ctx": num_ctx},
        }
        started = time.monotonic()
        try:
            resp = self._http.post("/api/chat", json=body)
        except httpx.HTTPError as exc:
            return InferenceCheck(False, time.monotonic() - started, "", type(exc).__name__)
        seconds = time.monotonic() - started
        if resp.status_code != 200:
            return InferenceCheck(False, seconds, "", f"HTTP {resp.status_code}: {resp.text[:200]}")
        reply = str(resp.json().get("message", {}).get("content", "")).strip()
        return InferenceCheck(bool(reply), seconds, reply[:200], None if reply else "empty reply")
