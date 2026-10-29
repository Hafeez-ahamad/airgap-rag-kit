"""Resource planner: hardware report + catalogue -> explainable, deterministic assessment.

Every number it produces is an ESTIMATE built from catalogue data and the assumptions listed in
``ASSUMPTIONS``. It never claims a model will run; a real inference check decides that.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from airgap.capacity.catalog import Catalog, LLMEntry, Runtime
from airgap.capacity.hardware import GIB, GpuInfo, HardwareReport, ReportSource
from airgap.capacity.kv import kv_cache_bytes, max_sessions

# Planning constants. Estimates, not measurements; each one is listed in ASSUMPTIONS.
OS_RESERVE_MIN = int(1.5 * GIB)
OS_RESERVE_FRACTION = 0.15
OS_RESERVE_MAX = 4 * GIB
SERVICES_BYTES = {"native": int(0.6 * GIB), "compose": int(1.5 * GIB)}
DATA_RESERVE_BYTES = 2 * GIB
DOWNLOAD_SLACK = 1.10
UNIFIED_GPU_FRACTION = 0.67
CPU_PREFERRED_MAX_PARAMS_B = 8.0
HIGH_CAPACITY_WEIGHTS = 12 * GIB
CONSTRAINED_RAM = 32 * GIB
WORKSTATION_GPU = 6 * GIB

ASSUMPTIONS = [
    "OS and desktop reserve: 15% of RAM, at least 1.5 GiB, at most 4 GiB (estimate).",
    "Platform services: native profile 0.6 GiB, compose profile 1.5 GiB (estimates, unmeasured).",
    "GGUF weights resident size equals the download size (estimate).",
    "Runtime host and GPU overheads come from the catalogue runtime entry (estimates).",
    "Retrieval models: budgeted at their measured loading peak (one sample, see catalogue).",
    "KV cache: 2 x layers x kv_heads x head_dim x bytes per element per token; sliding-window "
    "layers cap at their window (prediction only).",
    "Apple unified memory: GPU may use about 67% of RAM (estimate; macOS default varies).",
    "CPU-only: models above 8B parameters are ranked after smaller ones for interactive speed "
    "(product rule, speed not measured).",
    "Safety headroom is applied on top of every memory estimate.",
]


class Placement(StrEnum):
    GPU = "gpu"
    UNIFIED = "unified"
    PARTIAL_OFFLOAD = "partial_offload"
    CPU = "cpu"


class Tier(StrEnum):
    INSUFFICIENT_DATA = "insufficient_data"
    UNSUPPORTED = "unsupported"
    CONSTRAINED = "constrained"
    BALANCED = "balanced"
    GPU_WORKSTATION = "gpu_workstation"
    HIGH_CAPACITY = "high_capacity"


class Code(StrEnum):
    # rejections
    RAM_UNKNOWN = "ram_unknown"
    LICENCE_RESTRICTED = "licence_restricted"
    PLATFORM_UNSUPPORTED = "platform_unsupported"
    NO_RUNTIME_ARTIFACT = "no_runtime_artifact"
    CONTEXT_EXCEEDS_MODEL = "context_exceeds_model"
    INSUFFICIENT_MEMORY = "insufficient_memory"
    INSUFFICIENT_DISK = "insufficient_disk"
    # warnings
    PARTIAL_OFFLOAD = "partial_offload"
    CPU_ONLY = "cpu_only"
    NO_AVX2 = "no_avx2"
    CPU_FLAGS_UNKNOWN = "cpu_flags_unknown"
    NOT_ENOUGH_FREE_NOW = "not_enough_free_now"
    DISK_UNKNOWN = "disk_unknown"
    UNRANKED = "unranked"
    GPU_UNUSABLE = "gpu_unusable"
    RUNTIME_NOT_INSTALLED = "runtime_not_installed"
    GENERAL_ONLY = "general_assistant_only"


class Note(BaseModel):
    code: Code
    message: str


class PlannerOptions(BaseModel):
    context_tokens: int = Field(default=4096, ge=512, le=262144)
    kv_cache_type: str = "f16"
    runtime: str = "ollama"
    deployment: Literal["auto", "native", "compose"] = "auto"
    headroom_fraction: float = Field(default=0.10, ge=0.0, le=0.5)
    max_sessions_cap: int = Field(default=4, ge=1, le=64)
    allow_restricted_licences: bool = False
    document_assistant: bool = True
    installed_llm_ids: list[str] = Field(default_factory=list)
    retrieval_installed: bool = False


class Estimate(BaseModel):
    weights_bytes: int
    kv_per_session_bytes: int
    ram_needed_bytes: int
    vram_needed_bytes: int
    disk_needed_bytes: int
    gpu_layers_fraction: float  # share of the model on the GPU, 0..1 (estimate)


class ModelEvaluation(BaseModel):
    model_id: str
    display_name: str
    params_b: float
    quantization: str
    licence: str
    download_bytes: int
    eligible: bool
    fits_now: bool | None = None  # against currently available RAM; None if unknown
    placement: Placement | None = None
    max_sessions: int = 0
    estimate: Estimate | None = None
    rejections: list[Note] = Field(default_factory=list)
    warnings: list[Note] = Field(default_factory=list)
    ranked: bool = False


class MemoryBudget(BaseModel):
    ram_total_bytes: int | None
    ram_available_bytes: int | None
    os_reserve_bytes: int
    services_bytes: int
    retrieval_bytes: int
    llm_ram_budget_bytes: int  # what is left for the LLM process at full capacity
    llm_ram_budget_now_bytes: int | None  # same, against currently available RAM
    gpu_name: str | None
    gpu_usable_bytes: int  # 0 when no usable GPU
    headroom_fraction: float


class RuntimeSettings(BaseModel):
    runtime: str
    model_tag: str
    num_ctx: int
    num_parallel: int
    max_loaded_models: int
    kv_cache_type: str
    ingestion_batch_size: int
    ingestion_workers: int


class Assessment(BaseModel):
    source: ReportSource
    simulated: bool
    catalog_version: str
    report: HardwareReport
    options: PlannerOptions
    deployment: Literal["native", "compose"]
    tier: Tier
    document_assistant_available: bool
    budget: MemoryBudget
    recommended_model_id: str | None
    settings: RuntimeSettings | None
    evaluations: list[ModelEvaluation]
    warnings: list[Note]
    assumptions: list[str]
    retrieval_profile_id: str


def _os_reserve(total: int) -> int:
    return max(OS_RESERVE_MIN, min(int(total * OS_RESERVE_FRACTION), OS_RESERVE_MAX))


def _best_gpu(report: HardwareReport, runtime: Runtime) -> GpuInfo | None:
    usable = [g for g in report.gpus if g.usable_accelerator in runtime.accelerators]
    if not usable:
        return None
    return max(usable, key=lambda g: g.vram_total_bytes or 0)


def _deployment(report: HardwareReport, options: PlannerOptions) -> Literal["native", "compose"]:
    if options.deployment != "auto":
        return options.deployment
    docker = report.runtime("docker")
    big = (report.ram_total_bytes or 0) >= 16 * GIB
    if report.os == "linux" and big and docker is not None and docker.reachable:
        return "compose"
    return "native"  # no Docker/WSL requirement unless it is already healthy on a Linux server


def _with_headroom(n: int, headroom: float) -> int:
    return int(n * (1.0 + headroom))


class _Context:
    def __init__(self, report: HardwareReport, catalog: Catalog, options: PlannerOptions) -> None:
        self.report, self.catalog, self.options = report, catalog, options
        self.runtime = catalog.runtimes[options.runtime]
        self.bpe = self.runtime.kv_bytes_per_element[options.kv_cache_type]
        self.deployment = _deployment(report, options)
        self.gpu = _best_gpu(report, self.runtime)


def _budget(ctx: _Context, with_retrieval: bool) -> MemoryBudget:
    r, o = ctx.report, ctx.options
    total = r.ram_total_bytes or 0
    reserve = _os_reserve(total)
    services = SERVICES_BYTES[ctx.deployment]
    retrieval = ctx.catalog.retrieval.planning_bytes if with_retrieval else 0
    fixed = services + retrieval
    budget = max(0, total - reserve - fixed)
    now = None if r.ram_available_bytes is None else max(0, r.ram_available_bytes - fixed)
    gpu_usable = 0
    if ctx.gpu is not None and not ctx.gpu.unified_memory:
        free, total_vram = ctx.gpu.vram_free_bytes, ctx.gpu.vram_total_bytes
        vram = free if free is not None else (total_vram or 0)
        gpu_usable = max(0, vram - ctx.runtime.gpu_overhead_bytes)
    elif ctx.gpu is not None:  # unified: the GPU share of system RAM
        gpu_usable = int(total * UNIFIED_GPU_FRACTION)
    return MemoryBudget(
        ram_total_bytes=r.ram_total_bytes,
        ram_available_bytes=r.ram_available_bytes,
        os_reserve_bytes=reserve,
        services_bytes=services,
        retrieval_bytes=retrieval,
        llm_ram_budget_bytes=budget,
        llm_ram_budget_now_bytes=now,
        gpu_name=ctx.gpu.name if ctx.gpu else None,
        gpu_usable_bytes=gpu_usable,
        headroom_fraction=o.headroom_fraction,
    )


def _place(
    ctx: _Context, budget: MemoryBudget, weights: int, kv: int
) -> tuple[Placement, int, int, float] | None:
    """Placement, host RAM need, VRAM need, GPU share. None when nothing fits."""
    h = ctx.options.headroom_fraction
    host = ctx.runtime.host_overhead_bytes
    model = weights + kv
    gpu = ctx.gpu
    if gpu is not None and gpu.unified_memory:
        need = _with_headroom(host + model, h)
        if model <= budget.gpu_usable_bytes and need <= budget.llm_ram_budget_bytes:
            return Placement.UNIFIED, need, 0, 1.0
        return None
    if gpu is not None and budget.gpu_usable_bytes > 0:
        if _with_headroom(model, h) <= budget.gpu_usable_bytes:
            return Placement.GPU, _with_headroom(host, h), model, 1.0
        on_gpu = int(budget.gpu_usable_bytes / (1.0 + h))
        spill = model - on_gpu
        need = _with_headroom(host + spill, h)
        if need <= budget.llm_ram_budget_bytes:
            share = math.floor(on_gpu / model * 100) / 100  # never round up to 100%
            return Placement.PARTIAL_OFFLOAD, need, on_gpu, share
        return None
    need = _with_headroom(host + model, h)
    if need <= budget.llm_ram_budget_bytes:
        return Placement.CPU, need, 0, 0.0
    return None


def _disk_needed(ctx: _Context, model: LLMEntry, download: int) -> int:
    o = ctx.options
    llm = 0 if model.id in o.installed_llm_ids else download
    retrieval = 0
    if o.document_assistant and not o.retrieval_installed:
        retrieval = ctx.catalog.retrieval.download_bytes
    return int((llm + retrieval) * DOWNLOAD_SLACK) + DATA_RESERVE_BYTES


def _cpu_warnings(report: HardwareReport) -> list[Note]:
    if report.arch != "amd64":
        return []
    if report.cpu_flags is None:
        return [
            Note(
                code=Code.CPU_FLAGS_UNKNOWN,
                message="CPU features not inspected; CPU inference speed cannot be estimated.",
            )
        ]
    if "avx2" not in {f.lower() for f in report.cpu_flags}:
        return [Note(code=Code.NO_AVX2, message="CPU lacks AVX2: CPU inference will be very slow.")]
    return []


def evaluate_model(ctx: _Context, budget: MemoryBudget, model: LLMEntry) -> ModelEvaluation:
    r, o = ctx.report, ctx.options
    artifact = model.artifacts.get(o.runtime)
    ev = ModelEvaluation(
        model_id=model.id,
        display_name=model.display_name,
        params_b=model.params_b,
        quantization=model.quantization,
        licence=model.licence.id,
        download_bytes=artifact.download_bytes if artifact else 0,
        eligible=False,
    )
    rej = ev.rejections
    if r.ram_total_bytes is None:
        rej.append(Note(code=Code.RAM_UNKNOWN, message="Total RAM is unknown."))
    if not model.licence.allowed_by_default and not o.allow_restricted_licences:
        rej.append(
            Note(
                code=Code.LICENCE_RESTRICTED,
                message=f"Licence '{model.licence.id}' "
                "is not permitted by default (needs owner approval).",
            )
        )
    if artifact is None:
        rej.append(
            Note(
                code=Code.NO_RUNTIME_ARTIFACT, message=f"No {o.runtime} artifact in the catalogue."
            )
        )
    if r.platform not in ctx.runtime.platforms:
        rej.append(
            Note(
                code=Code.PLATFORM_UNSUPPORTED,
                message=f"{ctx.runtime.display_name} does not list platform '{r.platform}'.",
            )
        )
    if o.context_tokens > model.max_context:
        rej.append(
            Note(
                code=Code.CONTEXT_EXCEEDS_MODEL,
                message=f"Context {o.context_tokens} "
                f"exceeds the model maximum {model.max_context}.",
            )
        )
    if rej or artifact is None:
        return ev

    weights = model.weights_bytes
    kv = kv_cache_bytes(model.architecture, o.context_tokens, ctx.bpe)
    placed = _place(ctx, budget, weights, kv)
    disk = _disk_needed(ctx, model, artifact.download_bytes)
    if placed is None:
        rej.append(
            Note(
                code=Code.INSUFFICIENT_MEMORY,
                message=(
                    f"Needs about {(weights + kv) / GIB:.1f} GiB for weights and a "
                    f"{o.context_tokens}-"
                    f"token KV cache plus runtime overhead; about "
                    f"{budget.llm_ram_budget_bytes / GIB:.1f} GiB RAM"
                    + (
                        f" and {budget.gpu_usable_bytes / GIB:.1f} GiB VRAM"
                        if budget.gpu_usable_bytes
                        else ""
                    )
                    + " is available to the model (estimate)."
                ),
            )
        )
    if r.disk_free_bytes is not None and disk > r.disk_free_bytes:
        rej.append(
            Note(
                code=Code.INSUFFICIENT_DISK,
                message=(
                    f"Needs about {disk / GIB:.1f} GiB free disk; "
                    f"{r.disk_free_bytes / GIB:.1f} GiB free."
                ),
            )
        )
    if placed is None or rej:
        return ev

    placement, ram_need, vram_need, gpu_share = placed
    ev.eligible, ev.placement = True, placement
    ev.estimate = Estimate(
        weights_bytes=weights,
        kv_per_session_bytes=kv,
        ram_needed_bytes=ram_need,
        vram_needed_bytes=vram_need,
        disk_needed_bytes=disk,
        gpu_layers_fraction=gpu_share,
    )
    pool = budget.gpu_usable_bytes if placement is Placement.GPU else budget.llm_ram_budget_bytes
    used = vram_need if placement is Placement.GPU else ram_need
    spare = int((pool - used) / (1.0 + o.headroom_fraction))
    ev.max_sessions = 1 + max_sessions(spare, kv, o.max_sessions_cap - 1)
    if budget.llm_ram_budget_now_bytes is not None:
        ev.fits_now = ram_need <= budget.llm_ram_budget_now_bytes
        if not ev.fits_now:
            total_need = ram_need + budget.services_bytes + budget.retrieval_bytes
            ev.warnings.append(
                Note(
                    code=Code.NOT_ENOUGH_FREE_NOW,
                    message=(
                        f"Needs about {total_need / GIB:.1f} GiB of RAM for the app and model; "
                        f"{(r.ram_available_bytes or 0) / GIB:.1f} GiB is free right now. "
                        "Close other applications before starting this model."
                    ),
                )
            )
    if placement is Placement.PARTIAL_OFFLOAD:
        ev.warnings.append(
            Note(
                code=Code.PARTIAL_OFFLOAD,
                message=(
                    f"About {gpu_share:.0%} of the model fits in VRAM; the rest runs on the CPU, "
                    "which is much slower."
                ),
            )
        )
    if placement is Placement.CPU:
        ev.warnings.append(
            Note(code=Code.CPU_ONLY, message="Runs on the CPU only: expect slow responses.")
        )
        ev.warnings.extend(_cpu_warnings(r))
    if r.disk_free_bytes is None:
        ev.warnings.append(
            Note(
                code=Code.DISK_UNKNOWN,
                message="Free disk unknown; the download size could not be checked.",
            )
        )
    ev.ranked = model.quality_rank is not None
    if not ev.ranked:
        ev.warnings.append(
            Note(
                code=Code.UNRANKED,
                message="Answer quality not yet measured on this kit's evaluation set (unranked).",
            )
        )
    return ev


_PLACEMENT_ORDER = {
    Placement.GPU: 0,
    Placement.UNIFIED: 0,
    Placement.PARTIAL_OFFLOAD: 1,
    Placement.CPU: 2,
}


def _rank_key(ev: ModelEvaluation, model: LLMEntry) -> tuple[int, int, int, float, str]:
    measured = model.quality_rank if model.quality_rank is not None else 10**6
    assert ev.placement is not None
    slow_cpu = int(ev.placement is Placement.CPU and model.params_b > CPU_PREFERRED_MAX_PARAMS_B)
    return (measured, _PLACEMENT_ORDER[ev.placement], slow_cpu, -model.params_b, model.id)


def _tier(rec: ModelEvaluation | None, model: LLMEntry | None, budget: MemoryBudget) -> Tier:
    if budget.ram_total_bytes is None:
        return Tier.INSUFFICIENT_DATA
    if rec is None or model is None:
        return Tier.UNSUPPORTED
    if rec.placement is Placement.GPU:
        # Product rule: a small GPU on a low-RAM host is still a constrained device.
        if budget.gpu_usable_bytes < WORKSTATION_GPU or budget.ram_total_bytes < 16 * GIB:
            return Tier.CONSTRAINED
        heavy = model.weights_bytes >= HIGH_CAPACITY_WEIGHTS
        return Tier.HIGH_CAPACITY if heavy else Tier.GPU_WORKSTATION
    if rec.placement is Placement.UNIFIED:
        return Tier.HIGH_CAPACITY if budget.ram_total_bytes >= 48 * GIB else Tier.BALANCED
    # CPU or partial offload: constrained below 32 GiB RAM (product rule, not a measurement).
    return Tier.CONSTRAINED if budget.ram_total_bytes < CONSTRAINED_RAM else Tier.BALANCED


def _settings(
    ctx: _Context, tier: Tier, rec: ModelEvaluation | None, model: LLMEntry | None
) -> RuntimeSettings | None:
    if rec is None or model is None:
        return None
    constrained = tier in {Tier.CONSTRAINED, Tier.UNSUPPORTED}
    return RuntimeSettings(
        runtime=ctx.options.runtime,
        model_tag=model.artifacts[ctx.options.runtime].tag,
        num_ctx=ctx.options.context_tokens,
        num_parallel=1 if constrained else rec.max_sessions,
        max_loaded_models=1,
        kv_cache_type=ctx.options.kv_cache_type,
        ingestion_batch_size=8 if constrained else 32,
        ingestion_workers=1 if constrained else (4 if tier is Tier.HIGH_CAPACITY else 2),
    )


def _evaluate_all(
    ctx: _Context, with_retrieval: bool
) -> tuple[MemoryBudget, list[ModelEvaluation]]:
    budget = _budget(ctx, with_retrieval)
    return budget, [evaluate_model(ctx, budget, m) for m in ctx.catalog.llms]


def assess(
    report: HardwareReport, catalog: Catalog, options: PlannerOptions | None = None
) -> Assessment:
    """The one planning entry point (installer, CLI, setup API, Compatibility Lab)."""
    options = options or PlannerOptions()
    if options.runtime not in catalog.runtimes:
        raise ValueError(f"unknown runtime {options.runtime!r}")
    if options.kv_cache_type not in catalog.runtimes[options.runtime].kv_bytes_per_element:
        raise ValueError(f"unknown kv cache type {options.kv_cache_type!r}")
    ctx = _Context(report, catalog, options)
    warnings: list[Note] = []

    doc_assistant = options.document_assistant
    budget, evals = _evaluate_all(ctx, with_retrieval=doc_assistant)
    if doc_assistant and report.ram_total_bytes is not None and not any(e.eligible for e in evals):
        fallback_budget, fallback = _evaluate_all(ctx, with_retrieval=False)
        if any(e.eligible for e in fallback):
            budget, evals, doc_assistant = fallback_budget, fallback, False
            warnings.append(
                Note(
                    code=Code.GENERAL_ONLY,
                    message=(
                        "The Document Assistant's retrieval models do not fit alongside any LLM; "
                        "only the General Assistant can be offered on this device."
                    ),
                )
            )

    by_id = {m.id: m for m in catalog.llms}
    eligible = sorted(
        (e for e in evals if e.eligible), key=lambda e: _rank_key(e, by_id[e.model_id])
    )
    preferred = [e for e in eligible if e.fits_now is not False] or eligible
    rec = preferred[0] if preferred else None
    model = by_id[rec.model_id] if rec else None
    tier = _tier(rec, model, budget)

    if report.gpus and ctx.gpu is None:
        warnings.append(
            Note(
                code=Code.GPU_UNUSABLE,
                message=(
                    "A GPU was found but no supported driver/accelerator was detected for "
                    f"{ctx.runtime.display_name}; planning as CPU-only."
                ),
            )
        )
    rt = report.runtime(options.runtime)
    if report.source is ReportSource.REAL and (rt is None or not rt.installed):
        warnings.append(
            Note(
                code=Code.RUNTIME_NOT_INSTALLED,
                message=(
                    f"{ctx.runtime.display_name} is not installed; "
                    "setup will guide its installation."
                ),
            )
        )

    ordered = eligible + sorted((e for e in evals if not e.eligible), key=lambda e: e.model_id)
    return Assessment(
        source=report.source,
        simulated=report.source is ReportSource.SIMULATED,
        catalog_version=catalog.catalog_version,
        report=report,
        options=options,
        deployment=ctx.deployment,
        tier=tier,
        document_assistant_available=doc_assistant and rec is not None,
        budget=budget,
        recommended_model_id=rec.model_id if rec else None,
        settings=_settings(ctx, tier, rec, model),
        evaluations=ordered,
        warnings=warnings,
        assumptions=ASSUMPTIONS,
        retrieval_profile_id=catalog.retrieval.id,
    )


def plan_combination(
    report: HardwareReport,
    catalog: Catalog,
    model_ids: Sequence[str],
    options: PlannerOptions | None = None,
) -> dict[str, object]:
    """Co-residency check (spec F23): do these models fit together, each with >=1 session?"""
    options = options or PlannerOptions()
    ctx = _Context(report, catalog, options)
    budget = _budget(ctx, options.document_assistant)
    models = [catalog.llm(i) for i in model_ids]
    weights = sum(m.weights_bytes for m in models)
    kv = {m.id: kv_cache_bytes(m.architecture, options.context_tokens, ctx.bpe) for m in models}
    need = weights + sum(kv.values())
    h = options.headroom_fraction
    host = ctx.runtime.host_overhead_bytes * len(models)
    if budget.gpu_usable_bytes and ctx.gpu is not None and not ctx.gpu.unified_memory:
        pool, pool_name, required = budget.gpu_usable_bytes, "vram", _with_headroom(need, h)
        host_ok = _with_headroom(host, h) <= budget.llm_ram_budget_bytes
    else:
        pool, pool_name = budget.llm_ram_budget_bytes, "ram"
        required, host_ok = _with_headroom(need + host, h), True
    feasible = required <= pool and host_ok
    suggestions: list[str] = []
    if not feasible:
        suggestions = [
            "Use a smaller quantisation or a smaller model.",
            f"Shorten the context (now {options.context_tokens} tokens).",
            "Drop one model; load it on demand instead of keeping it resident.",
        ]
    remaining = max(0, pool - required)
    per_model = {
        m.id: 1
        + (
            max_sessions(remaining // len(models), kv[m.id], options.max_sessions_cap - 1)
            if feasible
            else 0
        )
        for m in models
    }
    return {
        "models": list(model_ids),
        "pool": pool_name,
        "pool_bytes": pool,
        "required_bytes": required,
        "feasible": feasible,
        "sessions_per_model": per_model,
        "suggestions": suggestions,
        "estimate": True,
    }
