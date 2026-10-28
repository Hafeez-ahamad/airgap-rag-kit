"""Qdrant adapter. The ACL filter is part of every search request (dense and sparse)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from airgap.core.ports import EmbeddedChunk, SparseVector
from airgap.core.types import Chunk, ScoredChunk

DENSE = "dense"
SPARSE = "bm25"


def acl_filter(acl_groups: Sequence[str]) -> qm.Filter:
    return qm.Filter(
        must=[qm.FieldCondition(key="acl_groups", match=qm.MatchAny(any=list(acl_groups)))]
    )


def _doc_filter(doc_id: str) -> qm.Filter:
    return qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])


def _payload(chunk: Chunk) -> dict[str, Any]:
    return {
        "doc_id": chunk.doc_id,
        "doc_name": chunk.doc_name,
        "version": chunk.version,
        "index": chunk.index,
        "text": chunk.text,
        "page": chunk.page,
        "page_end": chunk.page_end,
        "section": chunk.section,
        "language": chunk.language,
        "acl_groups": list(chunk.acl_groups),
    }


def _chunk(point_id: str, p: dict[str, Any]) -> Chunk:
    return Chunk(
        chunk_id=point_id,
        doc_id=p["doc_id"],
        doc_name=p["doc_name"],
        version=p["version"],
        index=p["index"],
        text=p["text"],
        page=p.get("page"),
        page_end=p.get("page_end"),
        section=p.get("section", ""),
        language=p.get("language", "und"),
        acl_groups=tuple(p.get("acl_groups", ())),
    )


class QdrantStore:
    def __init__(
        self, client: QdrantClient, collection: str, *, payload_indexes: bool = True
    ) -> None:
        """``payload_indexes=False`` only for qdrant-client's in-memory mode (tests)."""
        self._client = client
        self._collection = collection
        self._payload_indexes = payload_indexes
        self._known_to_exist = False

    def _exists(self) -> bool:
        if not self._known_to_exist:
            self._known_to_exist = self._client.collection_exists(self._collection)
        return self._known_to_exist

    def ensure_collection(self, dense_dim: int) -> None:
        if self._exists():
            return
        self._client.create_collection(
            self._collection,
            vectors_config={DENSE: qm.VectorParams(size=dense_dim, distance=qm.Distance.COSINE)},
            sparse_vectors_config={SPARSE: qm.SparseVectorParams(modifier=qm.Modifier.IDF)},
        )
        if self._payload_indexes:
            for key in ("doc_id", "acl_groups"):
                self._client.create_payload_index(
                    self._collection, key, field_schema=qm.PayloadSchemaType.KEYWORD
                )
            self._client.create_payload_index(
                self._collection, "version", field_schema=qm.PayloadSchemaType.INTEGER
            )
        self._known_to_exist = True

    def upsert(self, items: Sequence[EmbeddedChunk]) -> None:
        if not items:
            return
        points = [
            qm.PointStruct(
                id=item.chunk.chunk_id,
                vector={
                    DENSE: item.dense,
                    SPARSE: qm.SparseVector(indices=item.sparse.indices, values=item.sparse.values),
                },
                payload=_payload(item.chunk),
            )
            for item in items
        ]
        self._client.upsert(self._collection, points=points, wait=True)

    def delete_document(self, doc_id: str, keep_version: int | None = None) -> None:
        if not self._exists():
            return
        selector = _doc_filter(doc_id)
        if keep_version is not None:
            selector.must_not = [
                qm.FieldCondition(key="version", match=qm.MatchValue(value=keep_version))
            ]
        self._client.delete(
            self._collection, points_selector=qm.FilterSelector(filter=selector), wait=True
        )

    def count_document_chunks(self, doc_id: str) -> int:
        if not self._exists():
            return 0
        return self._client.count(
            self._collection, count_filter=_doc_filter(doc_id), exact=True
        ).count

    def _query(
        self, query: Any, using: str, acl_groups: Sequence[str], limit: int
    ) -> list[ScoredChunk]:
        if not acl_groups:  # no groups means no access, never "no filter"
            return []
        if not self._exists():
            return []  # nothing ingested yet
        result = self._client.query_points(
            self._collection,
            query=query,
            using=using,
            query_filter=acl_filter(acl_groups),
            limit=limit,
            with_payload=True,
        )
        return [
            ScoredChunk(_chunk(str(p.id), dict(p.payload or {})), float(p.score))
            for p in result.points
        ]

    def search_dense(
        self, vector: list[float], acl_groups: Sequence[str], limit: int
    ) -> list[ScoredChunk]:
        return self._query(vector, DENSE, acl_groups, limit)

    def search_sparse(
        self, vector: SparseVector, acl_groups: Sequence[str], limit: int
    ) -> list[ScoredChunk]:
        if not vector.indices:
            return []
        query = qm.SparseVector(indices=vector.indices, values=vector.values)
        return self._query(query, SPARSE, acl_groups, limit)
