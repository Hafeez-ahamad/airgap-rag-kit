"""Prerequisite checks -> explicit, consent-gated actions.

This module only DETECTS and EXPLAINS. It never installs system software, edits ``.wslconfig``,
firewall rules, boot settings or environment variables. Actions that need administrator rights,
a licence acceptance, a reboot or a vendor installer are returned for the user to perform.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from enum import StrEnum

from pydantic import BaseModel

from airgap.capacity.catalog import Catalog
from airgap.capacity.hardware import GpuVendor, HardwareReport
from airgap.capacity.planner import Assessment


class CheckStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    UNHEALTHY = "unhealthy"
    OUTDATED = "outdated"
    UNKNOWN = "unknown"
    NOT_REQUIRED = "not_required"


class ActionKind(StrEnum):
    MANUAL = "manual"  # user does it (vendor installer, start an app)
    ADMIN = "admin"  # needs administrator rights
    REBOOT = "reboot"  # needs a restart afterwards
    CONSENT = "consent"  # the app can do it after explicit approval (e.g. package download)


class Action(BaseModel):
    kind: ActionKind
    title: str
    instructions: str
    url: str | None = None
    needs_network: bool = False


class Check(BaseModel):
    id: str
    title: str
    required: bool
    status: CheckStatus
    detail: str | None = None
    actions: list[Action] = []

    @property
    def blocking(self) -> bool:
        return self.required and self.status not in {CheckStatus.OK, CheckStatus.NOT_REQUIRED}


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split(".") if p.isdigit())


def _ollama(report: HardwareReport, catalog: Catalog) -> Check:
    rt = catalog.runtimes["ollama"]
    info = report.runtime("ollama")
    install = Action(
        kind=ActionKind.MANUAL,
        title="Install Ollama",
        instructions=(
            "Download and run the official Ollama installer for your OS. It is the local LLM "
            "runtime; it does not need administrator rights on Windows or macOS. On Linux the "
            "official script needs sudo."
        ),
        url=rt.install_url,
        needs_network=True,
    )
    if info is None or not info.installed:
        return Check(
            id="ollama",
            title="Local LLM runtime (Ollama)",
            required=True,
            status=CheckStatus.MISSING,
            actions=[install],
        )
    if info.version and _version_tuple(info.version) < _version_tuple(rt.min_version):
        return Check(
            id="ollama",
            title="Local LLM runtime (Ollama)",
            required=True,
            status=CheckStatus.OUTDATED,
            detail=f"version {info.version}; need {rt.min_version} or later",
            actions=[install.model_copy(update={"title": "Update Ollama"})],
        )
    if not info.reachable:
        return Check(
            id="ollama",
            title="Local LLM runtime (Ollama)",
            required=True,
            status=CheckStatus.UNHEALTHY,
            detail="installed but not answering on 127.0.0.1",
            actions=[
                Action(
                    kind=ActionKind.MANUAL,
                    title="Start Ollama",
                    instructions="Open the Ollama app, or run `ollama serve` in a "
                    "terminal, then re-check.",
                )
            ],
        )
    tested = info.version in rt.tested_versions
    return Check(
        id="ollama",
        title="Local LLM runtime (Ollama)",
        required=True,
        status=CheckStatus.OK,
        detail=f"version {info.version}" + ("" if tested else " (not the tested version)"),
    )


def _ollama_privacy(report: HardwareReport) -> Check:
    return Check(
        id="ollama-privacy",
        title="Ollama cloud features off",
        required=False,
        status=CheckStatus.UNKNOWN,
        detail="Ollama 0.40+ can contact ollama.com. The app cannot read the runtime's settings.",
        actions=[
            Action(
                kind=ActionKind.MANUAL,
                title="Disable Ollama cloud access",
                instructions=(
                    "Set the environment variable OLLAMA_NO_CLOUD=1 for the Ollama service and "
                    "restart it. The app does not change persistent environment settings for you."
                ),
            )
        ],
    )


def _docker(report: HardwareReport, assessment: Assessment) -> list[Check]:
    if assessment.deployment != "compose":
        return [
            Check(
                id="docker",
                title="Docker Engine",
                required=False,
                status=CheckStatus.NOT_REQUIRED,
                detail="the native profile does not use containers",
            )
        ]
    info = report.runtime("docker")
    if info is None or not info.installed:
        status, detail = CheckStatus.MISSING, None
    elif not info.reachable:
        status, detail = CheckStatus.UNHEALTHY, info.detail
    else:
        status, detail = CheckStatus.OK, f"server {info.version}"
    checks = [
        Check(
            id="docker",
            title="Docker Engine + Compose",
            required=True,
            status=status,
            detail=detail,
            actions=[]
            if status is CheckStatus.OK
            else [
                Action(
                    kind=ActionKind.ADMIN,
                    title="Install or start Docker Engine",
                    instructions="Install Docker Engine and the Compose plugin with your "
                    "distribution's "
                    "packages, then start the service (needs sudo).",
                    url="https://docs.docker.com/engine/install/",
                    needs_network=True,
                )
            ],
        )
    ]
    if report.os == "windows":
        wsl = report.runtime("wsl")
        ok = wsl is not None and wsl.installed
        checks.append(
            Check(
                id="wsl",
                title="WSL 2 (Windows only, container profile)",
                required=True,
                status=CheckStatus.OK if ok else CheckStatus.MISSING,
                actions=[]
                if ok
                else [
                    Action(
                        kind=ActionKind.REBOOT,
                        title="Enable WSL 2",
                        instructions="Run `wsl --install` in an administrator terminal "
                        "and restart. "
                        "Setup resumes where it stopped.",
                    )
                ],
            )
        )
    return checks


def _gpu_driver(report: HardwareReport) -> Check:
    nvidia = [g for g in report.gpus if g.vendor is GpuVendor.NVIDIA]
    if not nvidia:
        return Check(
            id="gpu-driver",
            title="GPU driver",
            required=False,
            status=CheckStatus.NOT_REQUIRED,
            detail="no NVIDIA GPU detected",
        )
    if all(g.driver_version for g in nvidia):
        return Check(
            id="gpu-driver",
            title="NVIDIA driver",
            required=False,
            status=CheckStatus.OK,
            detail=nvidia[0].driver_version,
        )
    return Check(
        id="gpu-driver",
        title="NVIDIA driver",
        required=False,
        status=CheckStatus.MISSING,
        detail="GPU found but no working driver; models will run on the CPU",
        actions=[
            Action(
                kind=ActionKind.ADMIN,
                title="Install the NVIDIA driver",
                instructions="Install the driver from NVIDIA or your OS vendor, then restart.",
                url="https://www.nvidia.com/Download/index.aspx",
                needs_network=True,
            )
        ],
    )


def _disk(report: HardwareReport, assessment: Assessment) -> Check:
    rec = next(
        (e for e in assessment.evaluations if e.model_id == assessment.recommended_model_id), None
    )
    if report.disk_free_bytes is None:
        return Check(
            id="disk",
            title="Free disk space",
            required=True,
            status=CheckStatus.UNKNOWN,
            detail="could not be measured",
        )
    need = rec.estimate.disk_needed_bytes if rec and rec.estimate else None
    if need is None:
        return Check(
            id="disk",
            title="Free disk space",
            required=True,
            status=CheckStatus.UNKNOWN,
            detail="no recommended model to size",
        )
    ok = report.disk_free_bytes >= need
    return Check(
        id="disk",
        title="Free disk space",
        required=True,
        status=CheckStatus.OK if ok else CheckStatus.MISSING,
        detail=f"{report.disk_free_bytes / 1024**3:.1f} GiB free, "
        f"about {need / 1024**3:.1f} GiB needed (estimate)",
    )


def _python_models(native: bool, find: Callable[[str], object | None]) -> Check:
    if not native:
        return Check(
            id="retrieval-runtime",
            title="Embedding runtime",
            required=False,
            status=CheckStatus.NOT_REQUIRED,
            detail="runs in the model-service container",
        )
    missing = [m for m in ("torch", "sentence_transformers") if find(m) is None]
    if not missing:
        return Check(
            id="retrieval-runtime",
            title="Embedding runtime (PyTorch)",
            required=True,
            status=CheckStatus.OK,
        )
    return Check(
        id="retrieval-runtime",
        title="Embedding runtime (PyTorch)",
        required=True,
        status=CheckStatus.MISSING,
        detail=f"missing Python packages: {', '.join(missing)}",
        actions=[
            Action(
                kind=ActionKind.CONSENT,
                title="Install the embedding runtime",
                instructions="Run `uv sync --extra models` in backend/ (downloads "
                "CPU PyTorch, about 1 GB on disk).",
                needs_network=True,
            )
        ],
    )


def _ocr(report: HardwareReport) -> Check:
    tesseract = report.runtime("tesseract")
    ok = tesseract is not None and tesseract.installed
    return Check(
        id="ocr",
        title="OCR for scanned PDFs (Tesseract)",
        required=False,
        status=CheckStatus.OK if ok else CheckStatus.MISSING,
        detail=None if ok else "scanned pages will be reported as having no text",
        actions=[]
        if ok
        else [
            Action(
                kind=ActionKind.MANUAL,
                title="Install Tesseract (optional)",
                instructions="Install Tesseract OCR with English and the language packs you need.",
                url="https://tesseract-ocr.github.io/tessdoc/Installation.html",
                needs_network=True,
            )
        ],
    )


def check_prerequisites(
    report: HardwareReport,
    assessment: Assessment,
    catalog: Catalog,
    find_module: Callable[[str], object | None] = importlib.util.find_spec,
) -> list[Check]:
    if assessment.simulated:
        raise ValueError("prerequisites are checked against real hardware only")
    native = assessment.deployment == "native"
    return [
        _ollama(report, catalog),
        _ollama_privacy(report),
        *_docker(report, assessment),
        _gpu_driver(report),
        _disk(report, assessment),
        _python_models(native and assessment.document_assistant_available, find_module),
        _ocr(report),
    ]
