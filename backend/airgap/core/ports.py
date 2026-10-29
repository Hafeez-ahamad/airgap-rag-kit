"""Ports: every external dependency sits behind one of these so tests can fake it."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from airgap.core.types import ChatMessage, Chunk, ScoredChunk


@dataclass(frozen=True, slots=True)
class GenerationOptions:
    max_tokens: int = 768
    temperature: float = 0.1
    num_ctx: int | None = None
    stop: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SparseVector:
    indices: list[int]
    values: list[float]


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    chunk: Chunk
    dense: list[float]
    sparse: SparseVector = field(default_factory=lambda: SparseVector([], []))


class LLMBackend(Protocol):
    """A text-generation runtime (Ollama, vLLM, fake)."""

    @property
    def model_id(self) -> str: ...

    def stream(self, messages: Sequence[ChatMessage], options: GenerationOptions) -> Iterator[str]:
        """Yield text deltas."""
        ...

    def complete(self, messages: Sequence[ChatMessage], options: GenerationOptions) -> str: ...

    def healthy(self) -> bool: ...


class Embedder(Protocol):
    @property
    def dim(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class Reranker(Protocol):
    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        """Relevance in [0, 1], one value per text, same order."""
        ...


class VectorStore(Protocol):
    def ensure_collection(self, dense_dim: int) -> None: ...

    def upsert(self, items: Sequence[EmbeddedChunk]) -> None: ...

    def delete_document(self, doc_id: str, keep_version: int | None = None) -> None:
        """Remove a document's chunks; with ``keep_version``, only other versions'."""
        ...

    def search_dense(
        self, vector: list[float], acl_groups: Sequence[str], limit: int
    ) -> list[ScoredChunk]: ...

    def search_sparse(
        self, vector: SparseVector, acl_groups: Sequence[str], limit: int
    ) -> list[ScoredChunk]: ...

    def count_document_chunks(self, doc_id: str) -> int: ...

    def set_document_acl(self, doc_id: str, acl_groups: Sequence[str]) -> None:
        """Replace the ACL groups on every chunk of a document (dense and sparse share it)."""
        ...


class OCREngine(Protocol):
    def available(self) -> bool: ...

    def image_to_text(self, png: bytes, languages: Sequence[str]) -> str: ...


class BlobStore(Protocol):
    """Original uploaded files, one per document version."""

    def put(self, doc_id: str, version: int, filename: str, data: bytes) -> str: ...

    def get(self, key: str) -> bytes: ...

    def delete_document(self, doc_id: str) -> None: ...


class JobQueue(Protocol):
    def enqueue(self, job: str) -> None: ...

    def dequeue(self, timeout_s: float) -> str | None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
