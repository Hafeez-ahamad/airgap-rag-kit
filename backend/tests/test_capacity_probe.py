"""Unit tests: real-probe parsing and failure paths, with every OS touchpoint faked."""

from __future__ import annotations

from pathlib import Path

import pytest

from airgap.capacity.hardware import GIB, GpuVendor, HardwareReport, ReportSource
from airgap.capacity.probe import (
    CommandResult,
    ProbeEnv,
    RealHardwareProvider,
    parse_meminfo,
    parse_nvidia_smi,
)

MEMINFO = """MemTotal:       16311536 kB
MemFree:         1022404 kB
MemAvailable:   12093328 kB
SwapTotal:       2097148 kB
"""
CPUINFO = """processor\t: 0
model name\t: Synthetic CPU 9000
physical id\t: 0
core id\t\t: 0
flags\t\t: fpu sse4_2 avx avx2 fma
processor\t: 1
model name\t: Synthetic CPU 9000
physical id\t: 0
core id\t\t: 1
flags\t\t: fpu sse4_2 avx avx2 fma
"""
SMI = "NVIDIA GeForce RTX 3050 Laptop GPU, 4096, 3950, 572.47, 8.6\n"


def _runner(table: dict[str, CommandResult | None]):  # type: ignore[no-untyped-def]
    calls: list[list[str]] = []

    def run(args: list[str], timeout_s: float) -> CommandResult | None:
        calls.append(args)
        assert timeout_s <= 15, "every probe command must have a short timeout"
        return table.get(args[0])

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _linux_env(**overrides: object) -> ProbeEnv:
    files = {"/proc/meminfo": MEMINFO, "/proc/cpuinfo": CPUINFO}
    env = ProbeEnv(
        system="Linux",
        machine="x86_64",
        release="6.8",
        run=_runner({"nvidia-smi": CommandResult(0, SMI), "ollama": None, "docker": None}),
        read_file=files.get,
        http=lambda url, t: None,
        cpu_count=lambda: 2,
        disk_free=lambda p: 50 * GIB,
    )
    for key, value in overrides.items():
        setattr(env, key, value)
    return env


def test_parse_meminfo() -> None:
    total, avail, swap = parse_meminfo(MEMINFO)
    assert total == 16311536 * 1024
    assert avail == 12093328 * 1024
    assert swap == 2097148 * 1024


def test_parse_nvidia_smi_handles_na_and_garbage() -> None:
    gpus = parse_nvidia_smi(SMI + "Broken line\nGPU X, [N/A], [N/A], 550.1, [N/A]\n")
    assert len(gpus) == 2
    assert gpus[0].vram_total_bytes == 4096 * 1024**2
    assert gpus[0].usable_accelerator == "cuda"
    assert gpus[1].vram_total_bytes is None
    assert gpus[1].compute_capability is None


def test_linux_probe_full(tmp_path: Path) -> None:
    report = RealHardwareProvider(tmp_path, env=_linux_env()).report()
    assert report.source is ReportSource.REAL
    assert (report.os, report.arch) == ("linux", "amd64")
    assert report.cpu_model == "Synthetic CPU 9000"
    assert report.cpu_cores == 2
    assert report.cpu_flags is not None and "avx2" in report.cpu_flags
    assert report.ram_available_bytes == 12093328 * 1024
    assert report.gpus[0].vendor is GpuVendor.NVIDIA
    assert report.gpu_probe_ok
    assert report.disk_free_bytes == 50 * GIB
    ollama = report.runtime("ollama")
    assert ollama is not None and ollama.installed is False and ollama.reachable is False
    docker = report.runtime("docker")
    assert docker is not None and docker.installed is False


def test_missing_permissions_and_tools_degrade_without_crashing(tmp_path: Path) -> None:
    def broken_disk(_p: Path) -> int:
        raise PermissionError("denied")

    env = _linux_env(
        read_file=lambda path: None,  # /proc unreadable
        run=_runner({"nvidia-smi": CommandResult(9, "")}),  # driver present but failing
        disk_free=broken_disk,
    )
    report = RealHardwareProvider(tmp_path, env=env).report()
    assert report.ram_total_bytes is None
    assert report.disk_free_bytes is None
    assert not report.gpu_probe_ok
    text = " ".join(report.limitations)
    assert "could not read system memory" in text
    assert "nvidia-smi failed" in text
    assert "total RAM unknown" in text
    assert "free disk" in text


def test_no_nvidia_smi_means_cpu_planning_with_stated_limitation(tmp_path: Path) -> None:
    env = _linux_env(run=_runner({}))
    report = RealHardwareProvider(tmp_path, env=env).report()
    assert report.gpus == [] and report.gpu_probe_ok
    assert any("nvidia-smi not found" in n for n in report.limitations)


def test_windows_probe_uses_injected_os_calls_and_checks_wsl(tmp_path: Path) -> None:
    run = _runner(
        {
            "nvidia-smi": CommandResult(0, SMI),
            "ollama": CommandResult(0, "ollama version is 0.40.12"),
            "docker": CommandResult(1, ""),  # CLI present, engine down (HTTP 500 case)
            "wsl": CommandResult(0, "Default Version: 2"),
        }
    )
    env = ProbeEnv(
        system="Windows",
        machine="AMD64",
        release="11",
        run=run,
        read_file=lambda p: None,
        http=lambda url, t: 200,
        cpu_count=lambda: 16,
        disk_free=lambda p: 19 * GIB,
        windows_memory=lambda: (int(7.42 * GIB), int(0.82 * GIB)),
        windows_cpu=lambda: ("AMD Ryzen 7 4800H", ["avx", "avx2"]),
    )
    report = RealHardwareProvider(tmp_path, env=env).report()
    assert report.os == "windows" and report.arch == "amd64"
    assert report.ram_available_bytes == int(0.82 * GIB)
    ollama = report.runtime("ollama")
    assert ollama is not None and ollama.version == "0.40.12" and ollama.reachable
    docker = report.runtime("docker")
    assert docker is not None and docker.installed and docker.reachable is False
    wsl = report.runtime("wsl")
    assert wsl is not None and wsl.installed
    commands = [c[0] for c in run.calls]
    assert set(commands) <= {"nvidia-smi", "ollama", "docker", "wsl", "tesseract"}


def test_wsl_not_checked_off_windows(tmp_path: Path) -> None:
    report = RealHardwareProvider(tmp_path, env=_linux_env()).report()
    assert report.runtime("wsl") is None


def test_apple_silicon_reports_unified_memory(tmp_path: Path) -> None:
    run = _runner({"sysctl": CommandResult(0, str(16 * GIB))})
    env = _linux_env(system="Darwin", machine="arm64", run=run)
    report = RealHardwareProvider(tmp_path, env=env).report()
    assert report.ram_total_bytes == 16 * GIB
    assert report.gpus[0].unified_memory and report.gpus[0].usable_accelerator == "metal"


@pytest.mark.parametrize(
    ("field", "value", "expect"),
    [
        ("ram_available_bytes", 32 * GIB, "available RAM exceeded total"),
        ("cpu_threads", 2, "threads set to cores"),
    ],
)
def test_contradictory_data_is_normalised_and_reported(field: str, value: int, expect: str) -> None:
    kwargs: dict[str, object] = {
        "source": ReportSource.SIMULATED,
        "ram_total_bytes": 16 * GIB,
        "cpu_cores": 8,
        "gpu_probe_ok": True,
        "disk_free_bytes": GIB,
        field: value,
    }
    report = HardwareReport.model_validate(kwargs)
    assert any(expect in n for n in report.limitations)
    if field == "ram_available_bytes":
        assert report.ram_available_bytes == 16 * GIB


def test_real_probe_on_this_machine_does_not_crash(tmp_path: Path) -> None:
    """Runs the real probe on whatever machine runs the tests (read-only commands only)."""
    report = RealHardwareProvider(tmp_path, ollama_url="http://127.0.0.1:9").report()
    assert report.source is ReportSource.REAL
    assert report.os in {"windows", "linux", "darwin", "unknown"}
