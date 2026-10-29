"""Installation job: pull the selected LLM(s), fetch/verify retrieval models, run a REAL
inference check, then mark setup complete. Progress reflects actual backend state.

Safety: refuses to start without a stored REAL assessment and an eligible selection
(the Compatibility Lab cannot reach this path). Idempotent: models already present are
reused; a re-run after a failure or reboot resumes from what is on disk.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from airgap.capacity.catalog import Catalog, RetrievalModel
from airgap.models_manager.downloads import Cancelled, Downloader, DownloadError, FileSpec
from airgap.models_manager.runtime_admin import PullCancelled, RuntimeAdmin, RuntimeError_
from airgap.setup.state import SetupStore, Step, StepStatus

log = logging.getLogger(__name__)
HF_BASE = "https://huggingface.co"


class InstallRefused(ValueError):
    pass


def retrieval_dir(models_dir: Path, model: RetrievalModel) -> Path:
    return models_dir / model.name.rsplit("/", 1)[-1]


def retrieval_files(models_dir: Path, catalog: Catalog, base_url: str = HF_BASE) -> list[FileSpec]:
    specs: list[FileSpec] = []
    for model in (catalog.retrieval.embedder, catalog.retrieval.reranker):
        root = retrieval_dir(models_dir, model)
        for f in model.files:
            specs.append(
                FileSpec(
                    url=f"{base_url}/{model.name}/resolve/{model.revision}/{f.path}",
                    dest=root / f.path,
                    size=f.bytes,
                    sha256=f.sha256,
                )
            )
    return specs


class InstallJob:
    def __init__(
        self,
        store: SetupStore,
        catalog: Catalog,
        admin: RuntimeAdmin,
        downloader: Downloader,
        models_dir: Path,
        model_ids: Sequence[str],
        *,
        retrieval_base_url: str = HF_BASE,
        retries: int = 2,
        on_complete: Callable[[str], None] | None = None,
    ) -> None:
        self.id = str(uuid.uuid4())
        self._store, self._catalog, self._admin = store, catalog, admin
        self._downloader, self._models_dir = downloader, models_dir
        self._model_ids = list(model_ids)
        self._base_url, self._retries, self._on_complete = retrieval_base_url, retries, on_complete
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self.status = "pending"
        self.error: str | None = None
        self.items: dict[str, dict[str, Any]] = {}
        self._validate()

    def _validate(self) -> None:
        state = self._store.load()
        a = state.assessment
        if a is None or a.simulated:
            raise InstallRefused("run a real hardware scan first")
        by_id = {e.model_id: e for e in a.evaluations}
        bad = [m for m in self._model_ids if m not in by_id or not by_id[m].eligible]
        if bad or not self._model_ids:
            raise InstallRefused(f"models not eligible on this device: {bad or '(none)'}")
        self._doc_assistant = a.document_assistant_available
        self._num_ctx = a.settings.num_ctx if a.settings else 4096

    # -- progress -------------------------------------------------------------------------

    def _item(self, key: str, **fields: Any) -> None:
        with self._lock:
            self.items.setdefault(key, {"status": "pending"}).update(fields)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "error": self.error,
                "items": {k: dict(v) for k, v in self.items.items()},
            }

    # -- run --------------------------------------------------------------------------------

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, name=f"install-{self.id[:8]}", daemon=True)
        thread.start()
        return thread

    def run(self) -> None:
        self.status = "running"
        self._store.set_step(Step.INSTALL, StepStatus.RUNNING, "downloading")
        try:
            tags = self._install_llms()
            if self._doc_assistant:
                self._install_retrieval()
            self._store.set_step(Step.INSTALL, StepStatus.VERIFIED, "artifacts present")
            self._verify(tags[0])
        # Persist first, then publish: pollers must never see a status the store lacks.
        except (Cancelled, PullCancelled):
            self._store.set_step(
                Step.INSTALL,
                StepStatus.NEEDS_ACTION,
                "cancelled; partial downloads are kept and resume on retry",
            )
            self.status = "cancelled"
        except (RuntimeError_, DownloadError, InstallRefused) as exc:
            self._store.set_step(Step.INSTALL, StepStatus.FAILED, str(exc))
            self.status, self.error = "failed", str(exc)
        except Exception:
            log.exception("install failed")
            self._store.set_step(Step.INSTALL, StepStatus.FAILED, "unexpected error")
            self.status, self.error = "failed", "unexpected error; see diagnostics"

    def _install_llms(self) -> list[str]:
        runtime = "ollama"
        present = {m.tag for m in self._admin.installed()}
        tags: list[str] = []
        for mid in self._model_ids:
            tag = self._catalog.llm(mid).artifacts[runtime].tag
            tags.append(tag)
            key = f"model:{mid}"
            if tag in present:
                self._item(key, status="verified", detail="already installed")
                continue
            self._item(key, status="running", detail=tag)

            def progress(s: str, done: int | None, total: int | None, key: str = key) -> None:
                self._item(key, status="running", detail=s, completed=done, total=total)

            for attempt in range(self._retries + 1):
                try:
                    self._admin.pull(tag, progress, self.cancel_event)
                    break
                except PullCancelled:
                    self._item(key, status="cancelled")
                    raise
                except RuntimeError_ as exc:
                    if attempt == self._retries:
                        self._item(key, status="failed", detail=str(exc))
                        raise
                    self._item(key, status="running", detail=f"retrying after: {exc}")
            if tag not in {m.tag for m in self._admin.installed()}:
                self._item(key, status="failed", detail="not present after pull")
                raise RuntimeError_(f"{tag} is not listed by the runtime after pulling")
            self._item(key, status="verified")
        return tags

    def _install_retrieval(self) -> None:
        specs = retrieval_files(self._models_dir, self._catalog, self._base_url)
        total = sum(s.size or 0 for s in specs)
        done_before = 0
        self._item("retrieval", status="running", completed=0, total=total)
        for spec in specs:

            def progress(done: int, _t: int | None, base: int = done_before) -> None:
                self._item("retrieval", completed=base + done)

            self._downloader.fetch(spec, progress, self.cancel_event)
            done_before += spec.size or 0
        self._item("retrieval", status="verified", completed=total)

    def _verify(self, tag: str) -> None:
        self._store.set_step(Step.VERIFY, StepStatus.RUNNING, f"loading {tag}")
        self._item("verify", status="running")
        check = self._admin.inference_check(tag, self._num_ctx)
        if not check.ok:
            self._item("verify", status="failed", detail=check.error)
            self._store.set_step(Step.VERIFY, StepStatus.FAILED, check.error)
            raise RuntimeError_(f"inference check failed: {check.error}")
        self._item("verify", status="verified", seconds=round(check.seconds, 1))
        active = self._model_ids[0]

        def finish(state: Any) -> None:
            state.selected_model_ids = self._model_ids
            state.active_model_id = active
            state.index_version = self._catalog.retrieval.index_version
            state.completed_at = datetime.now(UTC)

        self._store.update(finish)
        self._store.set_step(
            Step.VERIFY, StepStatus.VERIFIED, f"real inference OK in {check.seconds:.1f}s"
        )
        self.status = "verified"
        if self._on_complete:
            self._on_complete(active)
