"""Wire real adapters from Settings. Entry points: API (``asgi``) and worker (``worker``)."""

from __future__ import annotations

import logging
import signal
import tempfile
import time
from pathlib import Path
from types import FrameType

from fastapi import FastAPI
from qdrant_client import QdrantClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from airgap.adapters.model_service import RemoteEmbedder, RemoteReranker
from airgap.adapters.storage import FileBlobStore, RedisQueue, SystemClock
from airgap.api.app import create_app
from airgap.api.services import Services
from airgap.auth.audit import AuditLog
from airgap.auth.service import AuthService
from airgap.capacity.catalog import load_catalog
from airgap.capacity.probe import RealHardwareProvider
from airgap.chat.history import ChatStore
from airgap.chat.orchestrator import Orchestrator
from airgap.chat.routing import SingleModelRouter
from airgap.config import Settings, get_settings
from airgap.core.ports import GenerationOptions
from airgap.db.session import make_engine, make_session_factory
from airgap.ingestion.documents import DocumentService, UploadLimits
from airgap.ingestion.ocr import TesseractOCR
from airgap.ingestion.pipeline import IngestionPipeline, PipelineConfig, recover_interrupted
from airgap.llm_backends.ollama import OllamaBackend
from airgap.logging_setup import configure_logging
from airgap.models_manager.runtime_admin import OllamaAdmin
from airgap.offline import enforce_offline
from airgap.retrieval.gate import ConfidenceGate
from airgap.retrieval.qdrant_store import QdrantStore
from airgap.retrieval.retriever import HybridRetriever, RetrieverConfig
from airgap.setup.service import SetupService
from airgap.setup.state import SetupStore

log = logging.getLogger(__name__)
HEARTBEAT = Path(tempfile.gettempdir()) / "airgap-worker-heartbeat"


def _db_ok(sessions: sessionmaker[Session]) -> bool:
    with sessions() as db:
        db.execute(text("SELECT 1"))
    return True


def build_services(settings: Settings) -> Services:
    sessions = make_session_factory(make_engine(settings.resolved_database_url))
    qdrant = QdrantClient(url=settings.qdrant_url, timeout=30)
    store = QdrantStore(qdrant, settings.qdrant_collection)
    queue = RedisQueue(settings.redis_url)
    clock = SystemClock()
    embedder = RemoteEmbedder(settings.model_service_url)
    reranker = RemoteReranker(settings.model_service_url)
    llm = OllamaBackend(settings.ollama_url, settings.llm_model)
    retriever = HybridRetriever(
        store,
        embedder,
        reranker,
        ConfidenceGate(settings.confidence_threshold, settings.tier_threshold_offset),
        RetrieverConfig(
            settings.retrieval_candidates, settings.rerank_candidates, settings.context_chunks
        ),
    )
    documents = DocumentService(
        sessions,
        FileBlobStore(settings.blob_root),
        queue,
        store,
        clock,
        UploadLimits(settings.max_upload_bytes, settings.max_zip_uncompressed_bytes),
    )
    orchestrator = Orchestrator(
        retriever, SingleModelRouter(llm), GenerationOptions(num_ctx=settings.llm_num_ctx)
    )
    catalog = load_catalog()
    data_dir = settings.data_dir
    # In the compose profile the scan runs inside the API container, so it sees the container's
    # view (no nvidia-smi there). install.sh runs `airgap scan` on the host for the real decision.
    setup = SetupService(
        SetupStore(data_dir / "setup.json"),
        catalog,
        lambda: RealHardwareProvider(data_dir, settings.ollama_url),
        OllamaAdmin(settings.ollama_url),
        settings.models_dir,
        on_model_ready=lambda mid: llm.set_model(catalog.llm(mid).artifacts["ollama"].tag),
        ocr_languages=tuple(settings.ocr_languages),
    )
    return Services(
        settings=settings,
        documents=documents,
        chat=ChatStore(sessions, clock),
        orchestrator=orchestrator,
        auth=AuthService(sessions, clock),
        audit=AuditLog(sessions, clock, store_questions=settings.audit_store_questions),
        setup=setup,
        health_checks={
            "database": lambda: _db_ok(sessions),
            "queue": queue.ping,
            "vector_store": lambda: qdrant.get_collections() is not None,
            "llm": llm.healthy,
        },
    )


def asgi() -> FastAPI:
    """uvicorn --factory airgap.bootstrap:asgi"""
    enforce_offline()
    settings = get_settings()
    configure_logging()
    return create_app(build_services(settings))


def worker() -> None:
    enforce_offline()
    settings = get_settings()
    configure_logging()
    sessions = make_session_factory(make_engine(settings.resolved_database_url))
    queue = RedisQueue(settings.redis_url)
    pipeline = IngestionPipeline(
        sessions,
        FileBlobStore(settings.blob_root),
        QdrantStore(QdrantClient(url=settings.qdrant_url, timeout=60), settings.qdrant_collection),
        RemoteEmbedder(settings.model_service_url, timeout_s=600),
        TesseractOCR(settings.ocr_binary),
        SystemClock(),
        PipelineConfig(
            settings.chunk_max_chars,
            settings.chunk_overlap_chars,
            tuple(settings.ocr_languages),
            settings.ocr_min_text_chars,
        ),
    )
    running = True

    def _stop(_sig: int, _frame: FrameType | None) -> None:
        nonlocal running
        running = False

    recover_interrupted(sessions, queue)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("ingestion worker started")
    while running:
        HEARTBEAT.touch()  # liveness for the container health check
        try:
            job = queue.dequeue(timeout_s=2)
        except Exception:
            log.exception("queue unavailable; retrying")
            time.sleep(2)
            continue
        if job is not None:
            pipeline.process(job)
    log.info("ingestion worker stopped")


if __name__ == "__main__":
    worker()
