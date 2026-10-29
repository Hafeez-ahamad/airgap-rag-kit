"""First-run setup (real hardware) and the admin Compatibility Lab (simulated hardware)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from airgap.api.deps import AdminDep, ServicesDep, SetupDep
from airgap.api.services import Services
from airgap.capacity.planner import Assessment, PlannerOptions
from airgap.capacity.providers import SimulatedSpec, builtin_profiles
from airgap.setup.plan import InstallPlan, SelectionRefused
from airgap.setup.prerequisites import Check
from airgap.setup.service import SetupError, SetupService, diagnostic_report, report_markdown

router = APIRouter(tags=["setup"])
SIMULATION_BANNER = "SIMULATION - NOT A REAL HARDWARE SCAN"


def _setup(services: Services) -> SetupService:
    if services.setup is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "setup is not available")
    return services.setup


def _audit(services: Services, actor: str, action: str, **detail: object) -> None:
    if services.audit is not None:
        services.audit.record(actor, action, detail=dict(detail))


class ScanRequest(BaseModel):
    consent: bool = Field(description="The user approved a read-only hardware scan")
    context_tokens: int | None = Field(default=None, ge=512, le=262144)


class ModelSelection(BaseModel):
    model_ids: list[str] = Field(min_length=1, max_length=4)


# ---- real setup ------------------------------------------------------------------------


@router.get("/setup/state")
def setup_state(services: ServicesDep, _user: SetupDep) -> dict[str, Any]:
    setup = _setup(services)
    state = setup.store.load()
    data = setup.store.to_public(state)
    job = setup.job()
    data["job"] = job.snapshot() if job else None
    return data


@router.post("/setup/scan", response_model=Assessment)
async def scan(body: ScanRequest, services: ServicesDep, user: SetupDep) -> Assessment:
    if not body.consent:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the scan needs your approval")
    assessment = await run_in_threadpool(_setup(services).scan, body.context_tokens)
    _audit(services, user.username, "setup.scan", tier=assessment.tier.value)
    return assessment


@router.get("/setup/prerequisites", response_model=list[Check])
def prerequisites(services: ServicesDep, _user: SetupDep) -> list[Check]:
    try:
        return _setup(services).prerequisites()
    except SetupError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/setup/plan", response_model=InstallPlan)
def plan(body: ModelSelection, services: ServicesDep, _user: SetupDep) -> InstallPlan:
    """Dry run: what installation would do. Changes nothing."""
    try:
        return _setup(services).plan(body.model_ids)
    except SelectionRefused as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, {"message": str(exc), "reasons": exc.reasons}
        ) from exc
    except SetupError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/setup/install", status_code=202)
def install(body: ModelSelection, services: ServicesDep, user: SetupDep) -> dict[str, Any]:
    try:
        job = _setup(services).start_install(body.model_ids)
    except SelectionRefused as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, {"message": str(exc), "reasons": exc.reasons}
        ) from exc
    except (SetupError, ValueError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    _audit(services, user.username, "setup.install", models=body.model_ids)
    return job.snapshot()


@router.post("/setup/install/cancel")
def cancel_install(services: ServicesDep, user: SetupDep) -> dict[str, bool]:
    cancelled = _setup(services).cancel()
    if cancelled:
        _audit(services, user.username, "setup.install.cancel")
    return {"cancelled": cancelled}


@router.get("/setup/report")
def real_report(services: ServicesDep, _user: SetupDep) -> dict[str, Any]:
    a = _setup(services).store.load().assessment
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no hardware scan yet")
    return diagnostic_report(a)


# ---- admin: models ----------------------------------------------------------------------


@router.get("/admin/models")
def admin_models(services: ServicesDep, _admin: AdminDep) -> dict[str, Any]:
    """Catalogue with fit indicators from the last REAL scan (no scan: fit unknown)."""
    setup = _setup(services)
    state = setup.store.load()
    by_id = {e.model_id: e for e in state.assessment.evaluations} if state.assessment else {}
    installed = set(setup.installed_tags())
    models = []
    for m in setup.catalog.llms:
        ev = by_id.get(m.id)
        tag = m.artifacts["ollama"].tag
        models.append(
            {
                "id": m.id,
                "display_name": m.display_name,
                "params_b": m.params_b,
                "quantization": m.quantization,
                "licence": m.licence.id,
                "download_bytes": m.artifacts["ollama"].download_bytes,
                "tag": tag,
                "installed": tag in installed,
                "active": m.id == state.active_model_id,
                "fit": None if ev is None else ("fits" if ev.eligible else "does_not_fit"),
                "placement": ev.placement if ev else None,
                "reasons": [n.message for n in ev.rejections] if ev else [],
                "quality_rank": m.quality_rank,
                "notes": m.notes,
            }
        )
    return {
        "catalog_version": setup.catalog.catalog_version,
        "retrieval_profile": setup.catalog.retrieval.display_name,
        "index_version": state.index_version,
        "models": models,
    }


# ---- Compatibility Lab (simulated) ----------------------------------------------------


class LabRequest(BaseModel):
    profile_id: str | None = None
    spec: SimulatedSpec | None = None
    options: PlannerOptions = Field(default_factory=PlannerOptions)
    model_ids: list[str] | None = Field(default=None, max_length=4)


def _spec(body: LabRequest) -> SimulatedSpec:
    if (body.profile_id is None) == (body.spec is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "give exactly one of profile_id or spec"
        )
    if body.spec is not None:
        return body.spec
    profiles = builtin_profiles()
    if body.profile_id not in profiles:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown profile")
    return profiles[body.profile_id]


@router.get("/admin/compat/profiles")
def lab_profiles(_admin: AdminDep) -> dict[str, Any]:
    return {
        "banner": SIMULATION_BANNER,
        "profiles": [p.model_dump() for p in builtin_profiles().values()],
    }


@router.post("/admin/compat/assess", response_model=Assessment)
def lab_assess(body: LabRequest, services: ServicesDep, _admin: AdminDep) -> Assessment:
    try:
        return _setup(services).simulate(_spec(body), body.options)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/admin/compat/combination")
def lab_combination(body: LabRequest, services: ServicesDep, _admin: AdminDep) -> dict[str, Any]:
    if not body.model_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "model_ids required")
    try:
        result = _setup(services).simulate_combination(_spec(body), body.model_ids, body.options)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown model {exc}") from exc
    return {"simulated": True, "banner": SIMULATION_BANNER, **result}


@router.post("/admin/compat/report")
def lab_report(
    body: LabRequest, services: ServicesDep, _admin: AdminDep, format: str = "json"
) -> Any:
    assessment = _setup(services).simulate(_spec(body), body.options)
    if format == "markdown":
        return PlainTextResponse(report_markdown(assessment), media_type="text/markdown")
    return diagnostic_report(assessment)
