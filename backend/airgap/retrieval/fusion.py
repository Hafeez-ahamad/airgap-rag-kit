"""Reciprocal rank fusion of several ranked lists."""

from __future__ import annotations

from collections.abc import Sequence

from airgap.core.types import ScoredChunk

RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[ScoredChunk]], *, k: int = RRF_K, limit: int | None = None
) -> list[ScoredChunk]:
    scores: dict[str, float] = {}
    chunks: dict[str, ScoredChunk] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            cid = item.chunk.chunk_id
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            chunks.setdefault(cid, item)
    ordered = sorted(scores, key=lambda cid: (-scores[cid], cid))
    fused = [ScoredChunk(chunks[cid].chunk, scores[cid]) for cid in ordered]
    return fused[:limit] if limit is not None else fused
