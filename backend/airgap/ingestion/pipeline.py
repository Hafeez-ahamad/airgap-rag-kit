"""Process one ingestion job: parse -> chunk -> embed -> index, updating status."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from airgap.core.ports import BlobStore, Clock, EmbeddedChunk, Embedder, OCREngine, VectorStore
from airgap.core.types import Chunk, DocStatus
from airgap.db.models import Document
from airgap.ingestion.chunker import DocMeta, chunk_blocks
from airgap.ingestion.parsers import ParseError, parse
from airgap.ingestion.validate import FileType
from airgap.retrieval import bm25

log = logging.getLogger(__name__)
_EMBED_BATCH = 32


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    max_chars: int = 1200
    overlap_chars: int = 200
    ocr_languages: tuple[str, ...] = ("eng",)
    ocr_min_text_chars: int = 25


def index_text(chunk: Chunk) -> str:
    """Text used for embedding and BM25: section path gives the chunk its context."""
    return f"{chunk.section}\n{chunk.text}" if chunk.section else chunk.text


class IngestionPipeline:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        blobs: BlobStore,
        store: VectorStore,
        embedder: Embedder,
        ocr: OCREngine,
        clock: Clock,
        config: PipelineConfig,
    ) -> None:
        self._sessions, self._blobs, self._store = sessions, blobs, store
        self._embedder, self._ocr, self._clock, self._config = embedder, ocr, clock, config

    def _set_status(
        self, doc_id: str, status: DocStatus, error: str | None = None, **fields: object
    ) -> None:
        with self._sessions.begin() as db:
            doc = db.get(Document, doc_id)
            if doc is None:
                return
            doc.status, doc.error, doc.updated_at = status.value, error, self._clock.now()
            for key, value in fields.items():
                setattr(doc, key, value)

    def process(self, job: str) -> None:
        doc_id, _, version_s = job.partition(":")
        version = int(version_s)
        with self._sessions() as db:
            doc = db.get(Document, doc_id)
            if doc is None or doc.current_version != version:
                log.info("skipping superseded or deleted job", extra={"job": job})
                return
            blob_key = next(v.blob_key for v in doc.versions if v.version == version)
            meta = DocMeta(doc.id, doc.name, version, tuple(doc.acl_groups))
            file_type = FileType(doc.file_type)
        try:
            self._set_status(doc_id, DocStatus.PARSING)
            cfg = self._config
            parsed = parse(
                file_type,
                self._blobs.get(blob_key),
                ocr=self._ocr,
                ocr_languages=cfg.ocr_languages,
                ocr_min_text_chars=cfg.ocr_min_text_chars,
            )
            chunks = chunk_blocks(
                parsed.blocks, meta, max_chars=cfg.max_chars, overlap_chars=cfg.overlap_chars
            )
            if not chunks:
                hint = "" if self._ocr.available() else " (OCR is not available)"
                raise ParseError(f"no text found in document{hint}")
            self._set_status(doc_id, DocStatus.EMBEDDING, ocr_pages=parsed.ocr_pages)
            self._index(chunks)
            # New version is in place; now remove every other version's chunks.
            self._store.delete_document(doc_id, keep_version=version)
            self._set_status(doc_id, DocStatus.READY, chunk_count=len(chunks))
        except ParseError as exc:
            self._set_status(doc_id, DocStatus.FAILED, str(exc))
        except Exception:
            log.exception("ingestion failed", extra={"job": job})
            self._set_status(doc_id, DocStatus.FAILED, "internal error during ingestion")

    def _index(self, chunks: Sequence[Chunk]) -> None:
        self._store.ensure_collection(self._embedder.dim)
        for start in range(0, len(chunks), _EMBED_BATCH):
            batch = chunks[start : start + _EMBED_BATCH]
            texts = [index_text(c) for c in batch]
            dense = self._embedder.embed(texts)
            self._store.upsert(
                [
                    EmbeddedChunk(c, d, bm25.encode_document(t))
                    for c, d, t in zip(batch, dense, texts, strict=True)
                ]
            )
