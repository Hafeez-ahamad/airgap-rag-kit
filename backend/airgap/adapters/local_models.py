"""In-process embedder and reranker for the native profile (ADR-0009).

Loaded lazily on first use and shared, so the General Assistant never pays for them and
both models load at most once. Same loading code and offline guarantees as the model service.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path

from airgap.model_service.app import EmbedModel, RerankModel
from airgap.offline import check_offline_env, enforce_offline


class RetrievalModelsMissing(RuntimeError):
    pass


class LocalModels:
    def __init__(
        self, embedder_dir: Path, reranker_dir: Path, device: str = "cpu", dtype: str = "bfloat16"
    ) -> None:
        self._emb_dir, self._rer_dir = embedder_dir, reranker_dir
        self._device, self._dtype = device, dtype
        self._lock = threading.Lock()
        self._embedder: EmbedModel | None = None
        self._reranker: RerankModel | None = None

    def present(self) -> bool:
        return all((d / "config.json").is_file() for d in (self._emb_dir, self._rer_dir))

    @property
    def loaded(self) -> bool:
        return self._embedder is not None

    def _load(self) -> None:
        with self._lock:
            if self._embedder is not None:
                return
            if not self.present():
                raise RetrievalModelsMissing("retrieval models are not installed; finish setup")
            enforce_offline()
            if check_offline_env():
                raise RuntimeError("offline environment variables not set")
            from airgap.model_service.app import _STEmbedder, _STReranker

            self._reranker = _STReranker(self._rer_dir, self._device, self._dtype)
            self._embedder = _STEmbedder(self._emb_dir, self._device, self._dtype)

    def embedder(self) -> LocalEmbedder:
        return LocalEmbedder(self)

    def reranker(self) -> LocalReranker:
        return LocalReranker(self)

    def _models(self) -> tuple[EmbedModel, RerankModel]:
        self._load()
        assert self._embedder is not None and self._reranker is not None
        return self._embedder, self._reranker

    def encode(self, texts: list[str]) -> list[list[float]]:
        return self._models()[0].encode(texts)

    def dim(self) -> int:
        return self._models()[0].dim()

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        return self._models()[1].predict(pairs)


class LocalEmbedder:
    def __init__(self, models: LocalModels) -> None:
        self._m = models

    @property
    def dim(self) -> int:
        return self._m.dim()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._m.encode(list(texts))


class LocalReranker:
    def __init__(self, models: LocalModels) -> None:
        self._m = models

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        return self._m.predict([(query, t) for t in texts])
