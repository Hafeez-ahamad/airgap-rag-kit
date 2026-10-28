"""Hybrid retrieval pipeline: dense + BM25 (both ACL-filtered) -> RRF -> rerank -> gate."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from airgap.core.ports import Embedder, Reranker, VectorStore
from airgap.core.types import ScoredChunk
from airgap.retrieval import bm25
from airgap.retrieval.fusion import reciprocal_rank_fusion
from airgap.retrieval.gate import ConfidenceGate, GateDecision


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    query: str
    reranked: list[ScoredChunk]  # best first, at most ``context_chunks``
    gate: GateDecision


@dataclass(frozen=True, slots=True)
class RetrieverConfig:
    candidates: int = 30
    rerank_candidates: int = 20
    context_chunks: int = 5


class HybridRetriever:
    def __init__(
        self,
        store: VectorStore,
        embedder: Embedder,
        reranker: Reranker,
        gate: ConfidenceGate,
        config: RetrieverConfig,
    ) -> None:
        self._store, self._embedder, self._reranker = store, embedder, reranker
        self._gate, self._config = gate, config

    def retrieve(self, query: str, acl_groups: Sequence[str]) -> RetrievalResult:
        cfg = self._config
        dense = self._store.search_dense(
            self._embedder.embed([query])[0], acl_groups, cfg.candidates
        )
        sparse = self._store.search_sparse(bm25.encode_query(query), acl_groups, cfg.candidates)
        fused = reciprocal_rank_fusion([dense, sparse], limit=cfg.rerank_candidates)
        if not fused:
            return RetrievalResult(query, [], self._gate.evaluate([]))
        scores = self._reranker.score(query, [c.chunk.text for c in fused])
        reranked = sorted(
            (ScoredChunk(c.chunk, s) for c, s in zip(fused, scores, strict=True)),
            key=lambda c: -c.score,
        )[: cfg.context_chunks]
        return RetrievalResult(query, reranked, self._gate.evaluate(reranked))
