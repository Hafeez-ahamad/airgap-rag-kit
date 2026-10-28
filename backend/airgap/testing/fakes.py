"""Deterministic fakes. They model behaviour well enough for pipeline tests,
not quality: never use them to produce reported numbers."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime

from airgap.core.ports import GenerationOptions
from airgap.core.types import ChatMessage, Role
from airgap.retrieval.bm25 import tokenize


class FakeEmbedder:
    """Hashed bag-of-words, L2-normalised. Shared words give cosine similarity > 0."""

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self._dim
            for token in tokenize(text):
                h = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big")
                vec[h % self._dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class FakeReranker:
    """Fraction of distinct query tokens present in the text."""

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        q = set(tokenize(query))
        if not q:
            return [0.0 for _ in texts]
        return [len(q & set(tokenize(t))) / len(q) for t in texts]


_DOC = re.compile(r'<document n="(\d+)"[^\n]*\n(.*?)\n</document>', re.DOTALL)
_QUESTION = re.compile(r"Question: (.*)$", re.DOTALL)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

Responder = Callable[[Sequence[ChatMessage]], str]


def extractive_answer(messages: Sequence[ChatMessage]) -> str:
    """Quote the first sentence of the context block that best overlaps the question."""
    body = messages[-1].content
    if messages[-1].role is Role.USER and "could not be verified" in body:
        body = next(m.content for m in reversed(messages) if "<documents>" in m.content)
    docs = _DOC.findall(body)
    match = _QUESTION.search(body)
    if not docs or not match:
        return "I have no documents for this."
    q = set(tokenize(match.group(1)))
    n, text = max(docs, key=lambda d: len(q & set(tokenize(d[1]))))
    sentence = _SENTENCE.split(text.strip())[0]
    return f'{sentence} [{n}: "{sentence}"]'


class FakeLLM:
    """Scriptable LLM. ``responder`` handles answers; ``rewriter`` handles query rewrites."""

    def __init__(
        self,
        responder: Responder = extractive_answer,
        rewriter: Responder | None = None,
        model_id: str = "fake-llm",
    ) -> None:
        self._responder = responder
        self._rewriter = rewriter
        self._model_id = model_id
        self.calls: list[list[ChatMessage]] = []

    @property
    def model_id(self) -> str:
        return self._model_id

    def _is_rewrite(self, messages: Sequence[ChatMessage]) -> bool:
        return bool(messages) and "standalone question" in messages[0].content

    def complete(self, messages: Sequence[ChatMessage], options: GenerationOptions) -> str:
        self.calls.append(list(messages))
        if self._is_rewrite(messages):
            if self._rewriter is None:
                return messages[-1].content.rsplit("Last message: ", 1)[-1]
            return self._rewriter(messages)
        return self._responder(messages)

    def stream(self, messages: Sequence[ChatMessage], options: GenerationOptions) -> Iterator[str]:
        text = self.complete(messages, options)
        for i in range(0, len(text), 16):
            yield text[i : i + 16]

    def healthy(self) -> bool:
        return True


class FakeOCR:
    def __init__(self, text: str = "", available: bool = True) -> None:
        self._text, self._available = text, available
        self.calls = 0

    def available(self) -> bool:
        return self._available

    def image_to_text(self, png: bytes, languages: Sequence[str]) -> str:
        self.calls += 1
        return self._text


class FixedClock:
    def __init__(self, at: datetime | None = None) -> None:
        self._at = at or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._at


class MemoryQueue:
    def __init__(self) -> None:
        self.items: list[str] = []

    def enqueue(self, job: str) -> None:
        self.items.append(job)

    def dequeue(self, timeout_s: float) -> str | None:
        return self.items.pop(0) if self.items else None


class MemoryBlobStore:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def put(self, doc_id: str, version: int, filename: str, data: bytes) -> str:
        key = f"{doc_id}/{version}/{filename}"
        self.data[key] = data
        return key

    def get(self, key: str) -> bytes:
        return self.data[key]

    def delete_document(self, doc_id: str) -> None:
        for key in [k for k in self.data if k.startswith(f"{doc_id}/")]:
            del self.data[key]
