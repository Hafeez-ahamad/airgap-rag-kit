from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from qdrant_client import QdrantClient
from sqlalchemy.orm import Session, sessionmaker

from airgap.db.models import Base
from airgap.db.session import make_engine, make_session_factory
from airgap.ingestion.documents import DocumentService, UploadLimits
from airgap.ingestion.pipeline import IngestionPipeline, PipelineConfig
from airgap.retrieval.gate import ConfidenceGate
from airgap.retrieval.qdrant_store import QdrantStore
from airgap.retrieval.retriever import HybridRetriever, RetrieverConfig
from airgap.testing.fakes import (
    FakeEmbedder,
    FakeOCR,
    FakeReranker,
    FixedClock,
    MemoryBlobStore,
    MemoryQueue,
)


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def store() -> Iterator[QdrantStore]:
    client = QdrantClient(location=":memory:")  # qdrant-client local mode, no server
    yield QdrantStore(client, "test_chunks")
    client.close()


class Kit:
    """All Phase 1 services wired with fakes."""

    def __init__(self, sessions: sessionmaker[Session], store: QdrantStore) -> None:
        self.sessions = sessions
        self.store = store
        self.blobs = MemoryBlobStore()
        self.queue = MemoryQueue()
        self.embedder = FakeEmbedder()
        self.reranker = FakeReranker()
        self.ocr = FakeOCR()
        self.clock = FixedClock()
        self.documents = DocumentService(
            sessions,
            self.blobs,
            self.queue,
            store,
            self.clock,
            UploadLimits(max_bytes=1_000_000, max_uncompressed=10_000_000),
        )
        self.pipeline = IngestionPipeline(
            sessions,
            self.blobs,
            store,
            self.embedder,
            self.ocr,
            self.clock,
            PipelineConfig(max_chars=600, overlap_chars=100),
        )
        self.retriever = HybridRetriever(
            store, self.embedder, self.reranker, ConfidenceGate(0.5), RetrieverConfig()
        )

    def drain(self) -> None:
        while (job := self.queue.dequeue(0)) is not None:
            self.pipeline.process(job)


@pytest.fixture
def kit(sessions: sessionmaker[Session], store: QdrantStore) -> Kit:
    return Kit(sessions, store)
