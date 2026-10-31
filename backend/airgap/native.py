"""Native (lean) deployment profile, ADR-0009: one process, no Docker, no WSL.

  SQLite metadata  +  embedded Qdrant (on-disk local mode)  +  in-process ingestion worker
  +  lazily loaded embedder/reranker  +  the host's Ollama  +  the built UI served at "/".

Listens on 127.0.0.1 by default. Same services, ports and tests as the compose profile; only
the adapters differ.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from qdrant_client import QdrantClient
from sqlalchemy.orm import Session, sessionmaker

from airgap.adapters.local_models import LocalModels
from airgap.adapters.storage import FileBlobStore, SystemClock, ThreadQueue
from airgap.api.app import create_app
from airgap.api.services import Services
from airgap.auth.audit import AuditLog
from airgap.auth.limits import RateLimiter
from airgap.auth.service import AuthService
from airgap.capacity.catalog import load_catalog
from airgap.capacity.probe import RealHardwareProvider
from airgap.chat.history import ChatStore
from airgap.chat.orchestrator import Orchestrator
from airgap.chat.routing import SingleModelRouter
from airgap.config import Settings
from airgap.core.ports import Embedder, GenerationOptions, LLMBackend, Reranker
from airgap.db.session import make_engine, make_session_factory
from airgap.ingestion.documents import DocumentService, UploadLimits
from airgap.ingestion.ocr import TesseractOCR
from airgap.ingestion.pipeline import IngestionPipeline, PipelineConfig, recover_interrupted
from airgap.llm_backends.ollama import OllamaBackend
from airgap.models_manager.runtime_admin import OllamaAdmin, RuntimeAdmin
from airgap.offline import enforce_offline
from airgap.retrieval.gate import ConfidenceGate
from airgap.retrieval.qdrant_store import QdrantStore
from airgap.retrieval.retriever import HybridRetriever, RetrieverConfig
from airgap.setup.installer import retrieval_dir
from airgap.setup.service import SetupService
from airgap.setup.state import SetupStore

log = logging.getLogger(__name__)
BACKEND = Path(__file__).resolve().parents[1]
UI_DIST = BACKEND.parent / "frontend" / "dist"

# UI security headers (the compose profile sets the same in nginx).
_CSP = (
    "default-src 'self'; img-src 'self' blob: data:; frame-src 'self' blob:; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)


def migrate(database_url: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(cfg, "head")


class IngestionWorker:
    """Single background thread: bounded, sequential ingestion (constrained-device friendly)."""

    def __init__(
        self,
        pipeline: IngestionPipeline,
        queue: ThreadQueue,
        closers: list[Callable[[], object]] | None = None,
    ) -> None:
        self._pipeline, self._queue = pipeline, queue
        self._closers = closers or []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ingestion", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Finish the current job, then release the embedded stores' locks."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=30)
        for close in self._closers:
            try:
                close()
            except Exception:
                log.exception("error while closing a store")

    def _run(self) -> None:
        while not self._stop.is_set():
            job = self._queue.dequeue(timeout_s=1)
            if job is not None:
                self._pipeline.process(job)


def build_native(
    settings: Settings,
    *,
    embedder: Embedder | None = None,
    reranker: Reranker | None = None,
    llm: OllamaBackend | LLMBackend | None = None,
    admin: RuntimeAdmin | None = None,
) -> tuple[Services, IngestionWorker]:
    """Wire the native profile. Keyword overrides exist for tests (fake adapters)."""
    data = settings.data_dir.resolve()
    data.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{(data / 'airgap.db').as_posix()}"
    migrate(db_url)
    engine = make_engine(db_url)
    sessions: sessionmaker[Session] = make_session_factory(engine)
    clock = SystemClock()
    catalog = load_catalog()
    models_dir = settings.models_dir.resolve()

    qdrant = QdrantClient(path=str(data / "qdrant"))
    store = QdrantStore(qdrant, settings.qdrant_collection, payload_indexes=False)
    queue = ThreadQueue()
    blobs = FileBlobStore(data / "blobs")

    r = catalog.retrieval
    local = LocalModels(
        retrieval_dir(models_dir, r.embedder),
        retrieval_dir(models_dir, r.reranker),
        settings.model_device,
        settings.model_dtype,
    )
    embedder = embedder or local.embedder()
    reranker = reranker or local.reranker()

    setup_store = SetupStore(data / "setup.json")
    state = setup_store.load()
    model_tag = settings.llm_model
    if state.active_model_id:
        model_tag = catalog.llm(state.active_model_id).artifacts["ollama"].tag
    llm = llm or OllamaBackend(settings.ollama_url, model_tag)

    tier_constrained = state.assessment is not None and state.assessment.tier.value in {
        "constrained",
        "unsupported",
    }
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
        blobs,
        queue,
        store,
        clock,
        UploadLimits(settings.max_upload_bytes, settings.max_zip_uncompressed_bytes),
    )
    pipeline = IngestionPipeline(
        sessions,
        blobs,
        store,
        embedder,
        TesseractOCR(settings.ocr_binary),
        clock,
        PipelineConfig(
            settings.chunk_max_chars,
            settings.chunk_overlap_chars,
            tuple(settings.ocr_languages),
            settings.ocr_min_text_chars,
        ),
    )

    def on_ready(model_id: str) -> None:
        if isinstance(llm, OllamaBackend):
            llm.set_model(catalog.llm(model_id).artifacts["ollama"].tag)

    setup = SetupService(
        setup_store,
        catalog,
        lambda: RealHardwareProvider(data, settings.ollama_url),
        admin or OllamaAdmin(settings.ollama_url),
        models_dir,
        on_model_ready=on_ready,
        ocr_languages=tuple(settings.ocr_languages),
    )
    services = Services(
        settings=settings,
        documents=documents,
        chat=ChatStore(sessions, clock),
        orchestrator=Orchestrator(
            retriever, SingleModelRouter(llm), GenerationOptions(num_ctx=settings.llm_num_ctx)
        ),
        health_checks={
            "database": lambda: sessions().connection() is not None,
            "vector_store": lambda: qdrant.get_collections() is not None,
            "llm": llm.healthy,
            "retrieval_models": local.present,
        },
        auth=AuthService(sessions, clock),
        audit=AuditLog(sessions, clock, store_questions=settings.audit_store_questions),
        setup=setup,
        # Constrained devices: one generation at a time protects the host (load shedding).
        chat_limiter=RateLimiter(
            per_minute=30, max_concurrent=1, global_concurrent=1 if tier_constrained else 4
        ),
    )
    recover_interrupted(sessions, queue)
    return services, IngestionWorker(pipeline, queue, [qdrant.close, engine.dispose])


def create_native_app(
    settings: Settings | None = None, ui_dist: Path = UI_DIST, **overrides: Any
) -> FastAPI:
    enforce_offline()
    settings = settings or Settings(profile="native")
    services, worker = build_native(settings, **overrides)
    api = create_app(services)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        worker.start()
        yield
        worker.stop()

    root = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    root.state.services = services
    root.mount("/api", api)

    @root.middleware("http")
    async def headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    if (ui_dist / "index.html").is_file():
        root.mount("/assets", StaticFiles(directory=ui_dist / "assets"), name="assets")

        @root.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            return FileResponse(ui_dist / "index.html")
    else:
        log.warning("UI not built (run `npm run build` in frontend/); API only")

    return root
