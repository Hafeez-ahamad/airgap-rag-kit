"""SIMULATED-PROFILE tests for the planner (validation level 3).

These prove the planning logic is deterministic, explainable and conservative for synthetic
device specs. They prove nothing about whether a model really loads or how fast it runs.
"""

from __future__ import annotations

import pytest

from airgap.capacity.catalog import Architecture, load_catalog, parse_catalog
from airgap.capacity.hardware import ReportSource
from airgap.capacity.kv import kv_cache_bytes, max_sessions
from airgap.capacity.planner import (
    Assessment,
    Code,
    Placement,
    PlannerOptions,
    Tier,
    assess,
    plan_combination,
)
from airgap.capacity.providers import SimulatedGpu, SimulatedSpec, builtin_profiles

CATALOG = load_catalog()


def _assess(profile: str, **opts: object) -> Assessment:
    report = builtin_profiles()[profile].to_report()
    return assess(report, CATALOG, PlannerOptions.model_validate(opts))


def _eval(a: Assessment, model_id: str):  # type: ignore[no-untyped-def]
    return next(e for e in a.evaluations if e.model_id == model_id)


def _codes(notes: list) -> set[Code]:  # type: ignore[type-arg]
    return {n.code for n in notes}


# ---- catalogue -------------------------------------------------------------------------------


def test_catalogue_is_valid_and_every_llm_has_provenance() -> None:
    assert CATALOG.schema_version == 1
    for m in CATALOG.llms:
        assert m.architecture.source and m.licence.verified
        for art in m.artifacts.values():
            assert art.size_source
        assert m.quality_rank is None, "no bake-off has run, so no rank may be recorded"
    r = CATALOG.retrieval
    assert "measured" in r.resident_source
    for f in [*r.embedder.files, *r.reranker.files]:
        if f.path.endswith((".bin", ".safetensors")):
            assert f.sha256 and len(f.sha256) == 64


def test_catalogue_rejects_duplicates_and_unknown_runtimes() -> None:
    text = """
schema_version: 1
catalog_version: x
runtimes: {}
llms:
  - {id: a, display_name: A, family: f, params_b: 1, quantization: q, format: gguf,
     artifacts: {vllm: {tag: t, download_bytes: 1, size_source: s}},
     architecture: {layers: 1, kv_heads: 1, head_dim: 1, source: s}, max_context: 1024,
     licence: {id: mit, commercial_use: true, redistribution: true, verified: v},
     languages: [], capabilities: []}
retrieval_profiles: []
"""
    with pytest.raises(ValueError):
        parse_catalog(text)


# ---- KV maths (hand-computed) ------------------------------------------------------------


def test_kv_full_attention_matches_hand_computation() -> None:
    arch = CATALOG.llm("qwen3-4b-instruct-2507-q4km").architecture
    # 2 x 36 layers x 8 kv heads x 128 dim x 2 bytes x 4096 tokens
    assert kv_cache_bytes(arch, 4096, 2) == 2 * 36 * 8 * 128 * 2 * 4096 == 603_979_776
    assert kv_cache_bytes(arch, 4096, 1) == 603_979_776 // 2  # q8_0 KV halves it


def test_kv_hybrid_sliding_caps_windowed_layers() -> None:
    arch = CATALOG.llm("gpt-oss-20b-mxfp4").architecture
    per = 2 * 8 * 64 * 2
    assert kv_cache_bytes(arch, 4096, 2) == 12 * 4096 * per + 12 * 128 * per
    # Below the window it behaves like full attention
    assert kv_cache_bytes(arch, 100, 2) == 24 * 100 * per


def test_kv_rejects_negative_and_architecture_needs_window() -> None:
    arch = CATALOG.llm("qwen3-4b-instruct-2507-q4km").architecture
    with pytest.raises(ValueError):
        kv_cache_bytes(arch, -1, 2)
    with pytest.raises(ValueError):
        Architecture(layers=4, kv_heads=1, head_dim=1, attention="hybrid_sliding", source="s")


def test_max_sessions() -> None:
    assert max_sessions(10, 3, cap=8) == 3
    assert max_sessions(100, 3, cap=8) == 8
    assert max_sessions(-5, 3, cap=8) == 0


# ---- the five required synthetic profiles --------------------------------------------------


def test_every_assessment_is_marked_simulated() -> None:
    for pid in builtin_profiles():
        a = _assess(pid)
        assert a.simulated and a.source is ReportSource.SIMULATED
        assert a.report.profile_name == pid
        assert a.assumptions, "estimates must carry their assumptions"


def test_constrained_gpu_profile() -> None:
    a = _assess("constrained-gpu")
    assert a.tier is Tier.CONSTRAINED
    assert a.deployment == "native"  # no Docker/WSL forced on a small Windows laptop
    assert a.recommended_model_id is not None
    rec = _eval(a, a.recommended_model_id)
    # Rule: recommend a model that fits in free RAM now if any does; otherwise warn.
    if any(e.eligible and e.fits_now for e in a.evaluations):
        assert rec.fits_now is True
    else:
        assert Code.NOT_ENOUGH_FREE_NOW in _codes(rec.warnings)
    assert rec.placement in {Placement.GPU, Placement.PARTIAL_OFFLOAD}
    assert a.settings is not None and a.settings.num_parallel == 1
    assert a.settings.ingestion_workers == 1 and a.settings.ingestion_batch_size <= 8
    big = _eval(a, "qwen3-32b-q4km")
    assert not big.eligible and Code.INSUFFICIENT_MEMORY in _codes(big.rejections)
    # A model that fits only after freeing memory is still eligible but carries a warning.
    later = [e for e in a.evaluations if e.eligible and e.fits_now is False]
    for e in later:
        assert Code.NOT_ENOUGH_FREE_NOW in _codes(e.warnings)


def test_cpu_only_profile() -> None:
    a = _assess("cpu-only-16")
    assert a.tier is Tier.CONSTRAINED
    eligible = [e for e in a.evaluations if e.eligible]
    assert eligible and all(e.placement is Placement.CPU for e in eligible)
    assert all(Code.CPU_ONLY in _codes(e.warnings) for e in eligible)
    rec = CATALOG.llm(a.recommended_model_id or "")
    assert rec.params_b <= 8.0, "CPU-only rule prefers models of 8B or fewer parameters"
    assert not _eval(a, "gpt-oss-20b-mxfp4").eligible


def test_balanced_workstation_profile() -> None:
    a = _assess("balanced-32-12")
    assert a.tier is Tier.GPU_WORKSTATION
    rec = _eval(a, a.recommended_model_id or "")
    assert rec.placement is Placement.GPU
    assert rec.estimate is not None
    assert rec.estimate.vram_needed_bytes <= a.budget.gpu_usable_bytes
    assert a.document_assistant_available


def test_high_capacity_profile() -> None:
    a = _assess("high-64-24")
    assert a.tier is Tier.HIGH_CAPACITY
    rec = _eval(a, a.recommended_model_id or "")
    assert rec.placement is Placement.GPU and rec.max_sessions >= 1
    gpu_models = [e for e in a.evaluations if e.placement is Placement.GPU]
    assert len(gpu_models) >= 4


def test_apple_silicon_uses_unified_memory() -> None:
    a = _assess("apple-silicon-16")
    eligible = [e for e in a.evaluations if e.eligible]
    assert eligible and all(e.placement is Placement.UNIFIED for e in eligible)


def test_insufficient_unknown_device_recommends_nothing() -> None:
    a = _assess("insufficient-unknown")
    assert a.tier is Tier.INSUFFICIENT_DATA
    assert a.recommended_model_id is None and a.settings is None
    assert not a.document_assistant_available
    assert all(Code.RAM_UNKNOWN in _codes(e.rejections) for e in a.evaluations)
    assert Code.GPU_UNUSABLE in _codes(a.warnings)


# ---- rules and edge cases ---------------------------------------------------------------


def test_licence_gate_and_explicit_override() -> None:
    a = _assess("high-64-24")
    restricted = _eval(a, "qwen2.5-3b-instruct-q4km")
    assert not restricted.eligible
    assert Code.LICENCE_RESTRICTED in _codes(restricted.rejections)
    b = _assess("high-64-24", allow_restricted_licences=True)
    assert _eval(b, "qwen2.5-3b-instruct-q4km").eligible


def test_context_longer_than_model_is_rejected() -> None:
    a = _assess("high-64-24", context_tokens=65536)
    e = _eval(a, "qwen2.5-7b-instruct-q4km")
    assert Code.CONTEXT_EXCEEDS_MODEL in _codes(e.rejections)


def test_longer_context_never_increases_sessions() -> None:
    short = _assess("balanced-32-12", context_tokens=2048)
    long = _assess("balanced-32-12", context_tokens=16384)
    for e in long.evaluations:
        if e.eligible:
            assert e.max_sessions <= _eval(short, e.model_id).max_sessions


def test_insufficient_disk_is_a_rejection_with_numbers() -> None:
    spec = builtin_profiles()["balanced-32-12"].model_copy(update={"disk_free_gib": 4})
    a = assess(spec.to_report(), CATALOG)
    assert a.recommended_model_id is None and a.tier is Tier.UNSUPPORTED
    e = _eval(a, "qwen3-4b-instruct-2507-q4km")
    note = next(n for n in e.rejections if n.code is Code.INSUFFICIENT_DISK)
    assert "GiB free" in note.message


def test_installed_artifacts_need_no_download_space() -> None:
    spec = builtin_profiles()["balanced-32-12"].model_copy(update={"disk_free_gib": 4})
    a = assess(
        spec.to_report(),
        CATALOG,
        PlannerOptions(installed_llm_ids=["qwen3-4b-instruct-2507-q4km"], retrieval_installed=True),
    )
    assert _eval(a, "qwen3-4b-instruct-2507-q4km").eligible


def test_retrieval_models_compete_for_ram_and_fallback_is_explained() -> None:
    spec = SimulatedSpec(
        os="linux",
        arch="amd64",
        ram_total_gib=6,
        ram_available_gib=5,
        disk_free_gib=100,
        cpu_flags=["avx2"],
    )
    a = assess(spec.to_report(), CATALOG)
    assert not a.document_assistant_available
    assert Code.GENERAL_ONLY in _codes(a.warnings)
    assert a.recommended_model_id is not None
    assert a.budget.retrieval_bytes == 0


def test_below_minimum_is_unsupported_with_reasons() -> None:
    spec = SimulatedSpec(
        os="linux", arch="amd64", ram_total_gib=3, ram_available_gib=2, disk_free_gib=100
    )
    a = assess(spec.to_report(), CATALOG)
    assert a.tier is Tier.UNSUPPORTED and a.recommended_model_id is None
    assert all(e.rejections for e in a.evaluations)


def test_unknown_platform_is_rejected() -> None:
    spec = SimulatedSpec(ram_total_gib=64, disk_free_gib=100)  # os/arch unknown
    a = assess(spec.to_report(), CATALOG)
    assert a.recommended_model_id is None
    assert all(Code.PLATFORM_UNSUPPORTED in _codes(e.rejections) for e in a.evaluations)


def test_gpu_without_driver_is_not_used() -> None:
    spec = SimulatedSpec(
        os="linux",
        arch="amd64",
        ram_total_gib=32,
        disk_free_gib=200,
        gpus=[SimulatedGpu(vram_total_gib=24)],
    )  # no driver_version
    a = assess(spec.to_report(), CATALOG)
    assert Code.GPU_UNUSABLE in _codes(a.warnings)
    assert all(e.placement is not Placement.GPU for e in a.evaluations)


def test_missing_avx2_warns_on_cpu() -> None:
    spec = SimulatedSpec(
        os="linux", arch="amd64", ram_total_gib=16, disk_free_gib=100, cpu_flags=["sse4_2"]
    )
    a = assess(spec.to_report(), CATALOG)
    rec = _eval(a, a.recommended_model_id or "")
    assert Code.NO_AVX2 in _codes(rec.warnings)


@pytest.mark.parametrize("document_assistant", [True, False])
def test_more_ram_never_shrinks_the_eligible_set(document_assistant: bool) -> None:
    """Within one product mode. (Across modes it can shrink: below ~8 GiB the planner falls back
    to General-Assistant-only, which frees the retrieval models' RAM for the LLM.)"""
    previous: set[str] = set()
    for gib in range(2, 129, 2):
        spec = SimulatedSpec(os="linux", arch="amd64", ram_total_gib=gib, disk_free_gib=2000)
        a = assess(spec.to_report(), CATALOG, PlannerOptions(document_assistant=document_assistant))
        same_mode = a.document_assistant_available == document_assistant
        eligible = {e.model_id for e in a.evaluations if e.eligible} if same_mode else set()
        assert previous <= eligible, f"monotonicity broken at {gib} GiB"
        previous = eligible


def test_assessment_is_deterministic() -> None:
    a = _assess("constrained-gpu").model_dump()
    b = _assess("constrained-gpu").model_dump()
    assert a == b


def test_eligible_models_come_first_and_every_rejection_has_a_reason() -> None:
    a = _assess("constrained-gpu")
    flags = [e.eligible for e in a.evaluations]
    assert flags == sorted(flags, reverse=True)
    for e in a.evaluations:
        assert e.eligible or e.rejections
        assert (e.estimate is not None) == e.eligible


def test_unknown_options_are_refused() -> None:
    report = builtin_profiles()["cpu-only-16"].to_report()
    with pytest.raises(ValueError):
        assess(report, CATALOG, PlannerOptions(runtime="vllm"))
    with pytest.raises(ValueError):
        assess(report, CATALOG, PlannerOptions(kv_cache_type="fp8"))


# ---- co-residency planner (F23) ------------------------------------------------------


def test_co_residency_feasible_and_infeasible_with_suggestions() -> None:
    report = builtin_profiles()["balanced-32-12"].to_report()
    ok = plan_combination(report, CATALOG, ["qwen3-4b-instruct-2507-q4km", "qwen3-8b-q4km"])
    assert ok["feasible"] and ok["pool"] == "vram"
    sessions = ok["sessions_per_model"]
    assert isinstance(sessions, dict) and all(v >= 1 for v in sessions.values())
    bad = plan_combination(report, CATALOG, ["qwen3-14b-q4km", "qwen3-8b-q4km"])
    assert not bad["feasible"] and bad["suggestions"]


def test_recommendation_fits_free_memory_when_possible() -> None:
    spec = builtin_profiles()["constrained-gpu"].model_copy(update={"ram_available_gib": 7.5})
    a = assess(spec.to_report(), CATALOG)
    rec = _eval(a, a.recommended_model_id or "")
    assert rec.fits_now is True and Code.NOT_ENOUGH_FREE_NOW not in _codes(rec.warnings)


def test_retrieval_budget_uses_measured_loading_peak() -> None:
    r = CATALOG.retrieval
    assert r.peak_bytes is not None and r.peak_source and "measured" in r.peak_source
    a = _assess("balanced-32-12")
    assert a.budget.retrieval_bytes == max(r.resident_bytes, r.peak_bytes)
