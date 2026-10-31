"""Setup facade used by the API, the CLI and the Compatibility Lab.

Real scans: ``RealHardwareProvider`` -> ``assess`` -> stored in ``SetupStore``.
Lab:        ``SimulatedHardwareProvider`` -> the SAME ``assess`` -> returned, never stored,
            never able to start downloads or inference.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from airgap import __version__
from airgap.capacity.catalog import Catalog
from airgap.capacity.hardware import HardwareReport
from airgap.capacity.planner import Assessment, PlannerOptions, assess, plan_combination
from airgap.capacity.providers import HardwareProvider, SimulatedHardwareProvider, SimulatedSpec
from airgap.models_manager.downloads import Downloader
from airgap.models_manager.runtime_admin import RuntimeAdmin, RuntimeError_
from airgap.redact import redact_mapping, redact_text
from airgap.setup.installer import InstallJob, retrieval_files
from airgap.setup.plan import InstallPlan, build_plan
from airgap.setup.prerequisites import Check, check_prerequisites
from airgap.setup.state import SetupStore, Step, StepStatus


class SetupError(ValueError):
    pass


class SetupService:
    def __init__(
        self,
        store: SetupStore,
        catalog: Catalog,
        real_provider: Callable[[], HardwareProvider],
        admin: RuntimeAdmin,
        models_dir: Path,
        downloader_factory: Callable[[], Downloader] = Downloader,
        retrieval_base_url: str = "https://huggingface.co",
        on_model_ready: Callable[[str], None] | None = None,
        ocr_languages: Sequence[str] = ("eng", "hin"),
    ) -> None:
        self.store, self.catalog = store, catalog
        self._real_provider, self._admin = real_provider, admin
        self._models_dir, self._downloader_factory = models_dir, downloader_factory
        self._retrieval_base_url = retrieval_base_url
        self._on_model_ready = on_model_ready
        self._ocr_languages = ocr_languages
        self._job: InstallJob | None = None
        self._job_lock = threading.Lock()

    # ---- inventory of what is already on disk / in the runtime -----------------------------

    def installed_tags(self) -> list[str]:
        try:
            return [m.tag for m in self._admin.installed()]
        except RuntimeError_:
            return []

    def retrieval_installed(self) -> bool:
        """Cheap presence + size check; the installer does the full SHA-256 verification."""
        return all(
            s.dest.is_file() and (s.size is None or s.dest.stat().st_size == s.size)
            for s in retrieval_files(self._models_dir, self.catalog)
        )

    def _options(self, context_tokens: int | None = None) -> PlannerOptions:
        tags = set(self.installed_tags())
        installed = [
            m.id for m in self.catalog.llms if any(a.tag in tags for a in m.artifacts.values())
        ]
        opts: dict[str, Any] = {
            "installed_llm_ids": installed,
            "retrieval_installed": self.retrieval_installed(),
        }
        if context_tokens:
            opts["context_tokens"] = context_tokens
        return PlannerOptions(**opts)

    # ---- real path -----------------------------------------------------------------------

    def scan(self, context_tokens: int | None = None) -> Assessment:
        self.store.set_step(Step.SCAN, StepStatus.RUNNING, "scanning (read-only)")
        try:
            report = self._real_provider().report()
            assessment = assess(report, self.catalog, self._options(context_tokens))
        except Exception as exc:
            self.store.set_step(Step.SCAN, StepStatus.FAILED, type(exc).__name__)
            raise
        self.store.record_scan(assessment)
        checks = check_prerequisites(
            report, assessment, self.catalog, ocr_languages=self._ocr_languages
        )
        blocking = [c.id for c in checks if c.blocking]
        self.store.set_step(
            Step.PREREQUISITES,
            StepStatus.NEEDS_ACTION if blocking else StepStatus.VERIFIED,
            ", ".join(blocking) if blocking else "all required checks passed",
        )
        return assessment

    def _real(self) -> Assessment:
        a = self.store.load().assessment
        if a is None:
            raise SetupError("run a hardware scan first")
        return a

    def prerequisites(self) -> list[Check]:
        a = self._real()
        return check_prerequisites(a.report, a, self.catalog, ocr_languages=self._ocr_languages)

    def plan(self, model_ids: list[str]) -> InstallPlan:
        a = self._real()
        return build_plan(
            a,
            self.prerequisites(),
            self.catalog,
            model_ids,
            self.installed_tags(),
            self.retrieval_installed(),
        )

    def start_install(self, model_ids: list[str]) -> InstallJob:
        plan = self.plan(model_ids)  # raises SelectionRefused with reasons
        if not plan.ready:
            raise SetupError(f"resolve these first: {', '.join(plan.blocking)}")
        with self._job_lock:
            if self._job is not None and self._job.status == "running":
                raise SetupError("an installation is already running")
            self.store.set_step(Step.MODELS, StepStatus.VERIFIED, ", ".join(model_ids))
            job = InstallJob(
                self.store,
                self.catalog,
                self._admin,
                self._downloader_factory(),
                self._models_dir,
                model_ids,
                retrieval_base_url=self._retrieval_base_url,
                on_complete=self._on_model_ready,
            )
            self._job = job
        job.start()
        return job

    def job(self) -> InstallJob | None:
        return self._job

    def cancel(self) -> bool:
        if self._job is None or self._job.status != "running":
            return False
        self._job.cancel_event.set()
        return True

    # ---- Compatibility Lab -------------------------------------------------------------

    def simulate(self, spec: SimulatedSpec, options: PlannerOptions | None = None) -> Assessment:
        report = SimulatedHardwareProvider(spec).report()
        assessment = assess(report, self.catalog, options or PlannerOptions())
        assert assessment.simulated  # never stored, never installable
        return assessment

    def simulate_combination(
        self, spec: SimulatedSpec, model_ids: list[str], options: PlannerOptions | None = None
    ) -> dict[str, Any]:
        return plan_combination(
            SimulatedHardwareProvider(spec).report(), self.catalog, model_ids, options
        )


def diagnostic_report(
    assessment: Assessment, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Exportable, redacted report for bug reports (Lab or real)."""
    body = assessment.model_dump(mode="json")
    body["report"].pop("disk_path", None)
    return redact_mapping(
        {
            "kind": "SIMULATION - NOT A REAL HARDWARE SCAN"
            if assessment.simulated
            else "real hardware scan",
            "app_version": __version__,
            "generated_at": datetime.now(UTC).isoformat(),
            "assessment": body,
            **(extra or {}),
        }
    )


def report_markdown(assessment: Assessment) -> str:
    """Human-readable summary of a diagnostic report (redacted)."""
    r: HardwareReport = assessment.report
    banner = (
        "SIMULATION - NOT A REAL HARDWARE SCAN" if assessment.simulated else "Real hardware scan"
    )
    lines = [
        "# AirGap compatibility report",
        "",
        f"**{banner}**",
        "",
        f"- App version: {__version__}; catalogue {assessment.catalog_version}",
        f"- Profile: {r.profile_name or 'n/a'}; platform {r.platform}",
        f"- RAM: {_gib(r.ram_total_bytes)} total, {_gib(r.ram_available_bytes)} available",
        f"- Disk free: {_gib(r.disk_free_bytes)}",
        "- GPUs: "
        + (", ".join(f"{g.name} ({_gib(g.vram_total_bytes)})" for g in r.gpus) or "none detected"),
        f"- Tier: {assessment.tier.value}; deployment: {assessment.deployment}",
        f"- Document Assistant available: {assessment.document_assistant_available}",
        f"- Recommended model: {assessment.recommended_model_id or 'none'}",
        "",
        "| Model | Eligible | Placement | Sessions | Reasons / warnings |",
        "|---|---|---|---|---|",
    ]
    for e in assessment.evaluations:
        notes = "; ".join(n.code.value for n in [*e.rejections, *e.warnings])
        lines.append(
            f"| {e.model_id} | {'yes' if e.eligible else 'no'} | "
            f"{e.placement.value if e.placement else '-'} | {e.max_sessions} | {notes} |"
        )
    lines += [
        "",
        "## Warnings",
        *[f"- {w.message}" for w in assessment.warnings],
        "",
        "## Limitations",
        *[f"- {x}" for x in r.limitations],
        "",
        "## Assumptions (all figures are estimates)",
        *[f"- {x}" for x in assessment.assumptions],
    ]
    return redact_text("\n".join(lines) + "\n")


def _gib(v: int | None) -> str:
    return "unknown" if v is None else f"{v / 1024**3:.1f} GiB"
