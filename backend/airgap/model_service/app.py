"""Embedding + reranker service (internal network only).

Env:
  AIRGAP_EMBEDDER_PATH   local directory with the embedding model (default /models/embedder)
  AIRGAP_RERANKER_PATH   local directory with the reranker model  (default /models/reranker)
  AIRGAP_MODEL_DEVICE    cpu | cuda (default cpu)
  AIRGAP_MAX_BATCH       maximum texts per request (default 64)
"""

from __future__ import annotations

import math
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from airgap.offline import check_offline_env, enforce_offline


class EmbedModel(Protocol):
    def dim(self) -> int: ...
    def encode(self, texts: list[str]) -> list[list[float]]: ...


class RerankModel(Protocol):
    def predict(self, pairs: list[tuple[str, str]]) -> list[float]: ...


@dataclass
class Models:
    embedder: EmbedModel
    reranker: RerankModel
    embedder_name: str
    reranker_name: str


# TODO(verify): `local_files_only` on SentenceTransformer/CrossEncoder against the pinned
# sentence-transformers version once the model image is built (marker: model).
class _STEmbedder:
    def __init__(self, path: Path, device: str) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(str(path), device=device, local_files_only=True)

    def dim(self) -> int:
        return int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=16)
        return [[float(x) for x in v] for v in vectors]


class _STReranker:
    def __init__(self, path: Path, device: str) -> None:
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(str(path), device=device, local_files_only=True)

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        logits = self._model.predict(pairs, batch_size=16)
        return [1.0 / (1.0 + math.exp(-float(x))) for x in logits]  # sigmoid -> [0, 1]


def load_models() -> Models:
    enforce_offline()
    missing = check_offline_env()
    if missing:
        raise RuntimeError(f"offline variables not set: {missing}")
    device = os.environ.get("AIRGAP_MODEL_DEVICE", "cpu")
    emb = Path(os.environ.get("AIRGAP_EMBEDDER_PATH", "/models/embedder"))
    rer = Path(os.environ.get("AIRGAP_RERANKER_PATH", "/models/reranker"))
    for path in (emb, rer):
        if not (path / "config.json").is_file():
            raise RuntimeError(f"model directory {path} has no config.json (weights not installed)")
    return Models(_STEmbedder(emb, device), _STReranker(rer, device), emb.name, rer.name)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1)


class RerankRequest(BaseModel):
    query: str
    texts: list[str] = Field(min_length=1)


def create_app(models: Models | None = None) -> FastAPI:
    max_batch = int(os.environ.get("AIRGAP_MAX_BATCH", "64"))
    state: dict[str, Models] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        state["models"] = models or load_models()
        yield

    app = FastAPI(title="AirGap model service", lifespan=lifespan, docs_url=None, redoc_url=None)

    def _models() -> Models:
        if "models" not in state:
            raise HTTPException(503, "models not loaded")
        return state["models"]

    def _check(n: int) -> None:
        if n > max_batch:
            raise HTTPException(413, f"at most {max_batch} texts per request")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok" if "models" in state else "loading"}

    @app.get("/info")
    def info() -> dict[str, Any]:
        m = _models()
        return {
            "embedder": m.embedder_name,
            "reranker": m.reranker_name,
            "embedding_dim": m.embedder.dim(),
        }

    @app.post("/embed")
    def embed(req: EmbedRequest) -> dict[str, Any]:
        _check(len(req.texts))
        return {"vectors": _models().embedder.encode(req.texts)}

    @app.post("/rerank")
    def rerank(req: RerankRequest) -> dict[str, Any]:
        _check(len(req.texts))
        return {"scores": _models().reranker.predict([(req.query, t) for t in req.texts])}

    return app
