"""Dry-run installation plan: what setup WOULD do, with sizes and required actions.

Built only from a REAL assessment. Selecting a model the assessment rejected is refused,
with the planner's reasons, rather than silently "trying anyway".
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from airgap.capacity.catalog import Catalog
from airgap.capacity.planner import Assessment, Note
from airgap.setup.prerequisites import Check


class PlanStep(BaseModel):
    id: str
    title: str
    kind: str  # prerequisite | download | configure | verify
    bytes: int | None = None
    needs_network: bool = False
    needs_user: bool = False
    detail: str | None = None


class InstallPlan(BaseModel):
    dry_run: bool = True
    ready: bool  # no blocking prerequisite
    blocking: list[str]
    model_ids: list[str]
    steps: list[PlanStep]
    total_download_bytes: int
    warnings: list[Note]
    estimate: bool = True


class SelectionRefused(ValueError):
    def __init__(self, reasons: dict[str, list[str]]) -> None:
        super().__init__("selected models are not compatible with this device")
        self.reasons = reasons


def build_plan(
    assessment: Assessment,
    checks: Sequence[Check],
    catalog: Catalog,
    model_ids: Sequence[str],
    installed_llm_tags: Sequence[str] = (),
    retrieval_installed: bool = False,
) -> InstallPlan:
    if assessment.simulated:
        raise ValueError("an installation plan needs a real hardware scan")
    by_id = {e.model_id: e for e in assessment.evaluations}
    refused: dict[str, list[str]] = {}
    for mid in model_ids:
        ev = by_id.get(mid)
        if ev is None:
            refused[mid] = ["not in the catalogue"]
        elif not ev.eligible:
            refused[mid] = [n.message for n in ev.rejections]
    if not model_ids:
        refused["(none)"] = ["select at least one model"]
    if refused:
        raise SelectionRefused(refused)

    steps: list[PlanStep] = []
    blocking: list[str] = []
    for check in checks:
        if check.blocking:
            blocking.append(check.id)
        for action in check.actions:
            if check.status.value in {"ok", "not_required"}:
                continue
            steps.append(
                PlanStep(
                    id=f"prereq:{check.id}",
                    title=action.title,
                    kind="prerequisite",
                    needs_network=action.needs_network,
                    needs_user=action.kind.value != "consent",
                    detail=action.instructions,
                )
            )

    runtime = assessment.options.runtime
    total = 0
    warnings: list[Note] = list(assessment.warnings)
    for mid in model_ids:
        model = catalog.llm(mid)
        artifact = model.artifacts[runtime]
        if artifact.tag in installed_llm_tags:
            steps.append(
                PlanStep(
                    id=f"model:{mid}",
                    title=f"Reuse installed {model.display_name}",
                    kind="download",
                    bytes=0,
                    detail=artifact.tag,
                )
            )
        else:
            total += artifact.download_bytes
            steps.append(
                PlanStep(
                    id=f"model:{mid}",
                    title=f"Download {model.display_name}",
                    kind="download",
                    bytes=artifact.download_bytes,
                    needs_network=True,
                    detail=f"{artifact.tag} ({model.licence.id}); size {artifact.size_source}",
                )
            )
        warnings.extend(by_id[mid].warnings)
    if assessment.document_assistant_available:
        r = catalog.retrieval
        if retrieval_installed:
            steps.append(
                PlanStep(
                    id="retrieval",
                    title="Verify installed retrieval models",
                    kind="verify",
                    bytes=0,
                    detail=r.display_name,
                )
            )
        else:
            total += r.download_bytes
            steps.append(
                PlanStep(
                    id="retrieval",
                    title="Download retrieval models (platform-managed)",
                    kind="download",
                    bytes=r.download_bytes,
                    needs_network=True,
                    detail=f"{r.embedder.name}@{r.embedder.revision[:7]} and "
                    f"{r.reranker.name}@{r.reranker.revision[:7]}, SHA-256 verified",
                )
            )
    settings = assessment.settings
    steps.append(
        PlanStep(
            id="configure",
            title="Write runtime configuration",
            kind="configure",
            detail=None
            if settings is None
            else (
                f"context {settings.num_ctx}, parallel {settings.num_parallel}, "
                f"KV cache {settings.kv_cache_type}, "
                f"ingestion batch {settings.ingestion_batch_size}"
            ),
        )
    )
    steps.append(
        PlanStep(id="verify", title="Load the model and run a real inference test", kind="verify")
    )
    return InstallPlan(
        ready=not blocking,
        blocking=blocking,
        model_ids=list(model_ids),
        steps=steps,
        total_download_bytes=total,
        warnings=warnings,
    )
