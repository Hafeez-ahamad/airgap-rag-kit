"""HTTP clients for the local embedding/reranker service (airgap.model_service)."""

from __future__ import annotations

from collections.abc import Sequence

import httpx


class ModelServiceError(RuntimeError):
    pass


class _Client:
    def __init__(self, base_url: str, timeout_s: float = 120.0) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=httpx.Timeout(timeout_s, connect=5))

    def _post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        try:
            resp = self._http.post(path, json=body)
        except httpx.HTTPError as exc:
            raise ModelServiceError(f"model service unreachable: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise ModelServiceError(f"model service {path} returned {resp.status_code}")
        data = resp.json()
        assert isinstance(data, dict)
        return data


class RemoteEmbedder(_Client):
    def __init__(self, base_url: str, timeout_s: float = 120.0) -> None:
        super().__init__(base_url, timeout_s)
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        if self._dim is None:
            info = self._http.get("/info").json()
            self._dim = int(info["embedding_dim"])
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        data = self._post("/embed", {"texts": list(texts)})
        vectors = data["vectors"]
        assert isinstance(vectors, list)
        return [[float(x) for x in v] for v in vectors]


class RemoteReranker(_Client):
    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        data = self._post("/rerank", {"query": query, "texts": list(texts)})
        scores = data["scores"]
        assert isinstance(scores, list)
        return [float(s) for s in scores]
