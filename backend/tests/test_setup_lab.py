"""Setup flow, Compatibility Lab, installer and downloader.

Levels: Lab tests are SIMULATED-PROFILE tests (L3). Installer/downloader tests use MOCK
adapters (L4): a fake runtime and an in-process HTTP transport. Nothing here proves a real
model downloads, loads or answers.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from airgap.api.app import create_app
from airgap.api.services import Services
from airgap.capacity.catalog import load_catalog
from airgap.capacity.hardware import HardwareReport, ReportSource, RuntimeInfo
from airgap.capacity.providers import builtin_profiles
from airgap.chat.history import ChatStore
from airgap.chat.orchestrator import Orchestrator
from airgap.chat.routing import SingleModelRouter
from airgap.config import Settings
from airgap.core.ports import GenerationOptions
from airgap.models_manager.downloads import (
    Cancelled,
    ChecksumMismatch,
    Downloader,
    DownloadError,
    FileSpec,
)
from airgap.models_manager.runtime_admin import (
    InferenceCheck,
    InstalledModel,
    PullCancelled,
    RuntimeError_,
)
from airgap.redact import redact, redact_text
from airgap.setup.service import SetupService
from airgap.setup.state import SetupStore, SimulatedAssessmentRefused, Step, StepStatus
from airgap.testing.fakes import FakeLLM
from tests.conftest import Kit

CATALOG = load_catalog()


# ---- fakes --------------------------------------------------------------------------------


class FakeAdmin:
    """Mock runtime. Records pulls; can fail, hang until cancelled, or fail inference."""

    def __init__(
        self, installed: list[str] | None = None, fail_pulls: int = 0, inference_ok: bool = True
    ) -> None:
        self.tags = list(installed or [])
        self.fail_pulls, self.inference_ok = fail_pulls, inference_ok
        self.pulls: list[str] = []
        self.block = threading.Event()  # set: pull waits for cancellation

    def installed(self) -> list[InstalledModel]:
        return [InstalledModel(t, 1, "d") for t in self.tags]

    def pull(self, tag: str, progress: Any, cancel: threading.Event | None) -> None:
        self.pulls.append(tag)
        if self.fail_pulls:
            self.fail_pulls -= 1
            raise RuntimeError_("connection reset")
        progress("pulling", 50, 100)
        if self.block.is_set():
            assert cancel is not None
            cancel.wait(5)
            raise PullCancelled("cancelled")
        progress("success", 100, 100)
        self.tags.append(tag)

    def unload(self, tag: str) -> None:
        pass

    def inference_check(self, tag: str, num_ctx: int) -> InferenceCheck:
        if self.inference_ok:
            return InferenceCheck(True, 0.1, "ready")
        return InferenceCheck(False, 0.1, "", "model failed to load")


def _real_report(profile: str = "balanced-32-12", ollama: bool = True) -> HardwareReport:
    """A report marked REAL, built from synthetic numbers (stands in for the real probe)."""
    data = builtin_profiles()[profile].to_report().model_dump()
    data.update(source=ReportSource.REAL, profile_name=None, limitations=[])
    data["runtimes"] = [
        RuntimeInfo(
            name="ollama", installed=ollama, version="0.40.12", reachable=ollama
        ).model_dump()
    ]
    return HardwareReport.model_validate(data)


class _Provider:
    def __init__(self, report: HardwareReport) -> None:
        self._r = report

    def report(self) -> HardwareReport:
        return self._r


def _files_transport(models_dir: Path) -> httpx.MockTransport:
    """Serves every retrieval file with the catalogue's size; pinned SHA files can't match,
    so tests pre-place those (as an operator copying from a bundle would)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{}")

    return httpx.MockTransport(handler)


def _prepare_retrieval_dir(models_dir: Path) -> None:
    """Place large pinned files so only small metadata files are 'downloaded'."""
    from airgap.setup.installer import retrieval_files

    for spec in retrieval_files(models_dir, CATALOG):
        if spec.size is not None and spec.sha256 is None:
            spec.dest.parent.mkdir(parents=True, exist_ok=True)
            spec.dest.write_bytes(b"\0" * 0)  # wrong size -> will be refetched


def _setup_service(tmp_path: Path, admin: FakeAdmin, report: HardwareReport) -> SetupService:
    return SetupService(
        SetupStore(tmp_path / "setup.json"),
        CATALOG,
        lambda: _Provider(report),
        admin,
        tmp_path / "models",
        downloader_factory=lambda: Downloader(
            httpx.Client(transport=_files_transport(tmp_path)), retries=0, sleep=lambda s: None
        ),
        retrieval_base_url="http://bundle.invalid",
    )


def _services(kit: Kit, setup: SetupService) -> Services:
    return Services(
        settings=Settings(dev_auth=True),
        documents=kit.documents,
        chat=ChatStore(kit.sessions, kit.clock),
        orchestrator=Orchestrator(kit.retriever, SingleModelRouter(FakeLLM()), GenerationOptions()),
        health_checks={"database": lambda: True},
        setup=setup,
    )


# ---- Compatibility Lab ------------------------------------------------------------------


@pytest.fixture
def lab(kit: Kit, tmp_path: Path) -> tuple[TestClient, SetupService]:
    setup = _setup_service(tmp_path, FakeAdmin(), _real_report())
    return TestClient(create_app(_services(kit, setup))), setup


def test_lab_lists_builtin_profiles_with_banner(lab: tuple[TestClient, SetupService]) -> None:
    client, _ = lab
    body = client.get("/admin/compat/profiles").json()
    assert body["banner"] == "SIMULATION - NOT A REAL HARDWARE SCAN"
    ids = {p["id"] for p in body["profiles"]}
    assert {
        "constrained-gpu",
        "cpu-only-16",
        "balanced-32-12",
        "high-64-24",
        "insufficient-unknown",
    } <= ids


@pytest.mark.parametrize("profile", sorted(builtin_profiles()))
def test_lab_uses_the_real_planner_and_marks_results_simulated(
    lab: tuple[TestClient, SetupService], profile: str
) -> None:
    client, setup = lab
    resp = client.post("/admin/compat/assess", json={"profile_id": profile})
    assert resp.status_code == 200
    body = resp.json()
    assert body["simulated"] is True and body["source"] == "simulated"
    # Same function as the installer: compare with a direct call.
    direct = setup.simulate(builtin_profiles()[profile])
    assert body["recommended_model_id"] == direct.recommended_model_id
    assert body["tier"] == direct.tier.value


def test_lab_never_touches_setup_state_or_starts_work(
    lab: tuple[TestClient, SetupService], tmp_path: Path
) -> None:
    client, setup = lab
    for pid in builtin_profiles():
        client.post("/admin/compat/assess", json={"profile_id": pid})
        client.post("/admin/compat/report", json={"profile_id": pid})
    assert not (tmp_path / "setup.json").exists()
    assert setup.job() is None
    admin = setup._admin
    assert isinstance(admin, FakeAdmin) and admin.pulls == []


def test_simulated_assessment_cannot_be_stored_as_setup(tmp_path: Path) -> None:
    store = SetupStore(tmp_path / "s.json")
    a = SetupService(
        store, CATALOG, lambda: _Provider(_real_report()), FakeAdmin(), tmp_path
    ).simulate(builtin_profiles()["high-64-24"])
    with pytest.raises(SimulatedAssessmentRefused):
        store.record_scan(a)


def test_lab_custom_spec_validation_and_unsupported_result(
    lab: tuple[TestClient, SetupService],
) -> None:
    client, _ = lab
    bad = client.post("/admin/compat/assess", json={"spec": {"ram_total_gib": -4}})
    assert bad.status_code == 422
    both = client.post(
        "/admin/compat/assess", json={"profile_id": "cpu-only-16", "spec": {"os": "linux"}}
    )
    assert both.status_code == 422
    assert client.post("/admin/compat/assess", json={"profile_id": "nope"}).status_code == 404
    tiny = client.post(
        "/admin/compat/assess",
        json={"spec": {"os": "linux", "arch": "amd64", "ram_total_gib": 2, "disk_free_gib": 50}},
    ).json()
    assert tiny["tier"] == "unsupported" and tiny["recommended_model_id"] is None


def test_lab_combination_and_report_export(lab: tuple[TestClient, SetupService]) -> None:
    client, _ = lab
    combo = client.post(
        "/admin/compat/combination",
        json={
            "profile_id": "balanced-32-12",
            "model_ids": ["qwen3-4b-instruct-2507-q4km", "qwen3-8b-q4km"],
        },
    ).json()
    assert combo["simulated"] and combo["feasible"]
    md = client.post(
        "/admin/compat/report?format=markdown",
        json={
            "spec": {
                "os": "linux",
                "arch": "amd64",
                "ram_total_gib": 16,
                "disk_free_gib": 100,
                "cpu_model": "C:\\Users\\alice\\cpu",
            }
        },
    )
    assert md.status_code == 200
    assert "SIMULATION - NOT A REAL HARDWARE SCAN" in md.text
    assert "alice" not in md.text
    js = client.post("/admin/compat/report", json={"profile_id": "cpu-only-16"}).json()
    assert js["kind"].startswith("SIMULATION") and "disk_path" not in js["assessment"]["report"]


# ---- real setup flow (mock runtime) -----------------------------------------------------


def test_scan_requires_consent_and_is_persisted(kit: Kit, tmp_path: Path) -> None:
    setup = _setup_service(tmp_path, FakeAdmin(), _real_report())
    client = TestClient(create_app(_services(kit, setup)))
    assert client.post("/setup/scan", json={"consent": False}).status_code == 400
    scanned = client.post("/setup/scan", json={"consent": True}).json()
    assert scanned["simulated"] is False and scanned["source"] == "real"
    restarted = SetupStore(tmp_path / "setup.json").load()  # survives a restart
    assert restarted.assessment is not None
    assert restarted.steps[Step.SCAN].status is StepStatus.VERIFIED


def test_missing_runtime_blocks_install_with_actionable_steps(kit: Kit, tmp_path: Path) -> None:
    setup = _setup_service(tmp_path, FakeAdmin(), _real_report(ollama=False))
    client = TestClient(create_app(_services(kit, setup)))
    client.post("/setup/scan", json={"consent": True})
    checks = {c["id"]: c for c in client.get("/setup/prerequisites").json()}
    assert checks["ollama"]["status"] == "missing"
    assert checks["ollama"]["actions"][0]["url"].startswith("https://ollama.com")
    assert checks["docker"]["status"] == "not_required"  # native profile: no Docker forced
    plan = client.post("/setup/plan", json={"model_ids": ["qwen3-4b-instruct-2507-q4km"]}).json()
    assert plan["dry_run"] and not plan["ready"] and "ollama" in plan["blocking"]
    install = client.post("/setup/install", json={"model_ids": ["qwen3-4b-instruct-2507-q4km"]})
    assert install.status_code == 409


def test_plan_refuses_incompatible_model_with_reasons(kit: Kit, tmp_path: Path) -> None:
    setup = _setup_service(tmp_path, FakeAdmin(), _real_report("constrained-gpu"))
    client = TestClient(create_app(_services(kit, setup)))
    client.post("/setup/scan", json={"consent": True})
    resp = client.post("/setup/plan", json={"model_ids": ["qwen3-32b-q4km"]})
    assert resp.status_code == 422
    assert "qwen3-32b-q4km" in resp.json()["detail"]["reasons"]
    restricted = client.post("/setup/plan", json={"model_ids": ["qwen2.5-3b-instruct-q4km"]})
    assert restricted.status_code == 422


def test_install_end_to_end_with_mock_runtime(tmp_path: Path) -> None:
    admin = FakeAdmin()
    report = _real_report()
    setup = _setup_service(tmp_path, admin, report)
    # General-Assistant-only flow (retrieval models are covered by the downloader tests).
    setup.scan()
    state = setup.store.load()
    assert state.assessment is not None
    state.assessment.document_assistant_available = False
    setup.store.update(lambda s: setattr(s, "assessment", state.assessment))
    job = setup.start_install(["qwen3-4b-instruct-2507-q4km"])
    job_thread_wait(job)
    assert job.status == "verified", job.snapshot()
    assert admin.pulls == ["qwen3:4b-instruct-2507-q4_K_M"]
    final = SetupStore(tmp_path / "setup.json").load()
    assert final.complete and final.active_model_id == "qwen3-4b-instruct-2507-q4km"
    assert final.steps[Step.VERIFY].status is StepStatus.VERIFIED
    # Idempotent re-run: the model is reused, not pulled again.
    job2 = setup.start_install(["qwen3-4b-instruct-2507-q4km"])
    job_thread_wait(job2)
    assert admin.pulls == ["qwen3:4b-instruct-2507-q4_K_M"]


def job_thread_wait(job: Any, timeout: float = 10) -> None:
    import time

    deadline = time.monotonic() + timeout
    while job.status in {"pending", "running"} and time.monotonic() < deadline:
        time.sleep(0.02)


def _general_only(setup: SetupService) -> None:
    setup.scan()
    a = setup.store.load().assessment
    assert a is not None
    a.document_assistant_available = False
    setup.store.update(lambda s: setattr(s, "assessment", a))


def test_install_retries_transient_pull_errors_then_fails_cleanly(tmp_path: Path) -> None:
    admin = FakeAdmin(fail_pulls=5)
    setup = _setup_service(tmp_path, admin, _real_report())
    _general_only(setup)
    job = setup.start_install(["qwen3-4b-instruct-2507-q4km"])
    job_thread_wait(job)
    assert job.status == "failed" and len(admin.pulls) == 3  # 1 try + 2 retries
    state = setup.store.load()
    assert not state.complete and state.steps[Step.INSTALL].status is StepStatus.FAILED


def test_install_cancel_keeps_state_resumable(tmp_path: Path) -> None:
    admin = FakeAdmin()
    admin.block.set()
    setup = _setup_service(tmp_path, admin, _real_report())
    _general_only(setup)
    job = setup.start_install(["qwen3-4b-instruct-2507-q4km"])
    for _ in range(200):
        if admin.pulls:
            break
        threading.Event().wait(0.01)
    assert setup.cancel()
    job_thread_wait(job)
    assert job.status == "cancelled"
    assert setup.store.load().steps[Step.INSTALL].status is StepStatus.NEEDS_ACTION
    admin.block.clear()
    retry = setup.start_install(["qwen3-4b-instruct-2507-q4km"])
    job_thread_wait(retry)
    assert retry.status == "verified"


def test_failed_inference_check_never_marks_setup_complete(tmp_path: Path) -> None:
    setup = _setup_service(tmp_path, FakeAdmin(inference_ok=False), _real_report())
    _general_only(setup)
    job = setup.start_install(["qwen3-4b-instruct-2507-q4km"])
    job_thread_wait(job)
    assert job.status == "failed" and "inference check failed" in (job.error or "")
    state = setup.store.load()
    assert not state.complete and state.steps[Step.VERIFY].status is StepStatus.FAILED


def test_first_run_setup_is_local_only(kit: Kit, tmp_path: Path) -> None:
    from airgap.auth.service import AuthService

    setup = _setup_service(tmp_path, FakeAdmin(), _real_report())
    svc = _services(kit, setup)
    svc.settings = Settings(dev_auth=False, cookie_secure=False)
    svc.auth = AuthService(kit.sessions, kit.clock)
    local = TestClient(create_app(svc))
    assert local.get("/setup/state").status_code == 200
    remote = TestClient(create_app(svc), client=("192.168.1.50", 5000))
    assert remote.get("/setup/state").status_code == 403


# ---- downloader (mock HTTP) -------------------------------------------------------------

PAYLOAD = bytes(range(256)) * 4096  # 1 MiB
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _range_server(
    fail_first: int = 0, corrupt: bool = False
) -> tuple[httpx.MockTransport, list[str]]:
    seen: list[str] = []
    state = {"fail": fail_first}

    def handler(request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("range")
        seen.append(rng or "")
        if state["fail"]:
            state["fail"] -= 1
            raise httpx.ConnectError("reset")
        body = PAYLOAD if not corrupt else PAYLOAD[:-1] + b"X"
        if rng:
            start = int(rng.split("=")[1].rstrip("-"))
            return httpx.Response(206, content=body[start:])
        return httpx.Response(200, content=body)

    return httpx.MockTransport(handler), seen


def test_download_resumes_from_partial_file(tmp_path: Path) -> None:
    transport, seen = _range_server()
    dest = tmp_path / "w.bin"
    (tmp_path / "w.bin.part").write_bytes(PAYLOAD[:300_000])
    dl = Downloader(httpx.Client(transport=transport), sleep=lambda s: None)
    assert dl.fetch(FileSpec("http://x/w.bin", dest, len(PAYLOAD), SHA)) == "downloaded"
    assert seen == ["bytes=300000-"]
    assert dest.read_bytes() == PAYLOAD and not (tmp_path / "w.bin.part").exists()
    assert dl.fetch(FileSpec("http://x/w.bin", dest, len(PAYLOAD), SHA)) == "present"


def test_download_checksum_mismatch_removes_partial_and_fails(tmp_path: Path) -> None:
    transport, _ = _range_server(corrupt=True)
    dl = Downloader(httpx.Client(transport=transport), sleep=lambda s: None)
    with pytest.raises(ChecksumMismatch):
        dl.fetch(FileSpec("http://x/w.bin", tmp_path / "w.bin", len(PAYLOAD), SHA))
    assert not (tmp_path / "w.bin").exists() and not (tmp_path / "w.bin.part").exists()


def test_download_retries_with_backoff_then_gives_up(tmp_path: Path) -> None:
    sleeps: list[float] = []
    transport, _ = _range_server(fail_first=2)
    dl = Downloader(httpx.Client(transport=transport), retries=3, backoff_s=1, sleep=sleeps.append)
    assert dl.fetch(FileSpec("http://x/w.bin", tmp_path / "w.bin", len(PAYLOAD), SHA))
    assert sleeps == [1, 2]
    transport, _ = _range_server(fail_first=10)
    dl = Downloader(httpx.Client(transport=transport), retries=1, sleep=lambda s: None)
    with pytest.raises(DownloadError):
        dl.fetch(FileSpec("http://x/v.bin", tmp_path / "v.bin", len(PAYLOAD), SHA))


def test_download_cancel_keeps_partial_for_resume(tmp_path: Path) -> None:
    transport, _ = _range_server()
    cancel = threading.Event()
    cancel.set()
    dl = Downloader(httpx.Client(transport=transport), sleep=lambda s: None)
    with pytest.raises(Cancelled):
        dl.fetch(FileSpec("http://x/w.bin", tmp_path / "w.bin", len(PAYLOAD), SHA), cancel=cancel)
    assert not (tmp_path / "w.bin").exists()


# ---- redaction ---------------------------------------------------------------------------


def test_redaction_removes_personal_paths_and_secrets() -> None:
    text = (
        "C:\\Users\\alice\\models /home/bob/x /Users/carol/y mail me: a.b@example.org "
        "Authorization: Bearer abcdefghijkl12345 hf_ABCDEFGHIJKLMNOP "
        "AIRGAP_DB_PASSWORD=hunter2 postgres://user:pw@db/x"
    )
    out = redact_text(text)
    for leaked in (
        "alice",
        "bob",
        "carol",
        "a.b@example.org",
        "abcdefghijkl12345",
        "hf_ABCDEFGHIJKLMNOP",
        "hunter2",
        "user:pw",
    ):
        assert leaked not in out, leaked
    assert redact({"api_key": "x", "nested": [{"token": "y"}], "ok": 1}) == {
        "api_key": "<redacted>",
        "nested": [{"token": "<redacted>"}],
        "ok": 1,
    }
