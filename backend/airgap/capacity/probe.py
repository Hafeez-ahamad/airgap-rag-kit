"""Real hardware probe. Read-only: it runs only query commands, each with a timeout.

Every external touchpoint (commands, files, HTTP, OS calls) is injectable so the parsing and
the failure paths are unit-tested without the hardware.
"""

from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from airgap.capacity.hardware import (
    GpuInfo,
    GpuVendor,
    HardwareReport,
    ReportSource,
    RuntimeInfo,
)
from airgap.ingestion.ocr import default_locations, find_tesseract, parse_langs

MIB = 1024**2


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str


Runner = Callable[[list[str], float], CommandResult | None]  # None: executable not found
HttpGet = Callable[[str, float], int | None]  # status code, None on connection failure


def run_command(args: list[str], timeout_s: float) -> CommandResult | None:
    exe = shutil.which(args[0])
    if exe is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argument lists, no shell
            [exe, *args[1:]], capture_output=True, timeout=timeout_s, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return CommandResult(-1, "")
    raw = proc.stdout
    # wsl.exe writes UTF-16LE; everything else is UTF-8.
    text = (
        raw.decode("utf-16-le", "replace")
        if raw[1:2] == b"\x00"
        else raw.decode("utf-8", "replace")
    )
    return CommandResult(proc.returncode, text.replace("\x00", ""))


def http_status(url: str, timeout_s: float) -> int | None:
    try:
        return httpx.get(url, timeout=timeout_s).status_code
    except httpx.HTTPError:
        return None


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


@dataclass
class ProbeEnv:
    """Everything the probe touches. Tests replace fields; production uses the defaults."""

    system: str = field(default_factory=platform.system)
    machine: str = field(default_factory=platform.machine)
    release: str = field(default_factory=platform.release)
    run: Runner = run_command
    read_file: Callable[[str], str | None] = _read
    http: HttpGet = http_status
    cpu_count: Callable[[], int | None] = os.cpu_count
    disk_free: Callable[[Path], int] = lambda p: shutil.disk_usage(p).free
    windows_memory: Callable[[], tuple[int, int] | None] | None = None
    windows_cpu: Callable[[], tuple[str | None, list[str] | None]] | None = None
    ollama_paths: list[str] = field(default_factory=list)
    tesseract_paths: list[str] = field(default_factory=list)


def _os_name(system: str) -> str:
    return {"Windows": "windows", "Linux": "linux", "Darwin": "darwin"}.get(system, "unknown")


def _arch(machine: str) -> str:
    m = machine.lower()
    if m in {"x86_64", "amd64"}:
        return "amd64"
    if m in {"arm64", "aarch64"}:
        return "arm64"
    return "unknown"


# ---- memory ------------------------------------------------------------------------------


def parse_meminfo(text: str) -> tuple[int | None, int | None, int | None]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        m = re.match(r"(\w+):\s+(\d+)\s*kB", line)
        if m:
            values[m.group(1)] = int(m.group(2)) * 1024
    return values.get("MemTotal"), values.get("MemAvailable"), values.get("SwapTotal")


def _win_memory() -> tuple[int, int] | None:
    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    if sys.platform != "win32":
        return None
    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullTotalPhys), int(status.ullAvailPhys)


def _win_cpu() -> tuple[str | None, list[str] | None]:
    if sys.platform != "win32":
        return None, None
    import winreg

    name: str | None = None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        )
        name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except OSError:
        pass
    present = ctypes.windll.kernel32.IsProcessorFeaturePresent
    features = {"avx": 39, "avx2": 40, "avx512f": 41}
    return name, [f for f, code in features.items() if present(code)]


def _parse_cpuinfo(text: str) -> tuple[str | None, int | None, list[str] | None]:
    model = re.search(r"^model name\s*:\s*(.+)$", text, re.M)
    flags = re.search(r"^(?:flags|Features)\s*:\s*(.+)$", text, re.M)
    cores = {
        (p, c) for p, c in re.findall(r"physical id\s*:\s*(\d+).*?core id\s*:\s*(\d+)", text, re.S)
    }
    return (
        model.group(1).strip() if model else None,
        len(cores) or None,
        flags.group(1).split() if flags else None,
    )


# ---- GPUs ----------------------------------------------------------------------------------


def parse_nvidia_smi(text: str) -> list[GpuInfo]:
    gpus: list[GpuInfo] = []
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        name, total, free, driver, cc = parts[:5]

        def mib(v: str) -> int | None:
            return int(float(v) * MIB) if re.fullmatch(r"\d+(\.\d+)?", v) else None

        gpus.append(
            GpuInfo(
                vendor=GpuVendor.NVIDIA,
                name=name,
                vram_total_bytes=mib(total),
                vram_free_bytes=mib(free),
                driver_version=driver or None,
                compute_capability=cc if cc and cc != "[N/A]" else None,
            )
        )
    return gpus


_NVIDIA_QUERY = [
    "nvidia-smi",
    "--query-gpu=name,memory.total,memory.free,driver_version,compute_cap",
    "--format=csv,noheader,nounits",
]


class RealHardwareProvider:
    def __init__(
        self,
        data_dir: Path,
        ollama_url: str = "http://127.0.0.1:11434",
        env: ProbeEnv | None = None,
    ) -> None:
        self._data_dir, self._ollama_url = data_dir, ollama_url
        self._env = env or ProbeEnv(
            windows_memory=_win_memory,
            windows_cpu=_win_cpu,
            ollama_paths=[
                str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe")
            ],
            tesseract_paths=[str(p) for p in default_locations()],
        )

    def report(self) -> HardwareReport:
        env = self._env
        os_name = _os_name(env.system)
        notes: list[str] = []
        fields: dict[str, object] = {
            "source": ReportSource.REAL,
            "os": os_name,
            "os_version": env.release or None,
            "arch": _arch(env.machine),
            "cpu_threads": env.cpu_count(),
        }
        self._memory(os_name, fields, notes)
        self._cpu(os_name, fields, notes)
        gpus, gpu_ok = self._gpus(os_name, fields, notes)
        fields["gpus"], fields["gpu_probe_ok"] = gpus, gpu_ok
        fields["disk_free_bytes"], fields["disk_path"] = self._disk(notes)
        fields["runtimes"] = self._runtimes(os_name)
        fields["limitations"] = notes
        return HardwareReport.model_validate(fields)

    # -- pieces -----------------------------------------------------------------------------

    def _memory(self, os_name: str, out: dict[str, object], notes: list[str]) -> None:
        env = self._env
        if os_name == "linux":
            text = env.read_file("/proc/meminfo")
            if text:
                total, avail, swap = parse_meminfo(text)
                out.update(ram_total_bytes=total, ram_available_bytes=avail, swap_total_bytes=swap)
                if avail is None:
                    notes.append("MemAvailable missing from /proc/meminfo (old kernel?)")
                return
        elif os_name == "windows" and env.windows_memory is not None:
            mem = env.windows_memory()
            if mem:
                out.update(ram_total_bytes=mem[0], ram_available_bytes=mem[1])
                return
        elif os_name == "darwin":
            res = env.run(["sysctl", "-n", "hw.memsize"], 5)
            if res and res.returncode == 0 and res.stdout.strip().isdigit():
                out["ram_total_bytes"] = int(res.stdout.strip())
                notes.append("available RAM is not measured on macOS yet; using total only")
                return
        notes.append("could not read system memory")

    def _cpu(self, os_name: str, out: dict[str, object], notes: list[str]) -> None:
        env = self._env
        if os_name == "linux":
            text = env.read_file("/proc/cpuinfo")
            if text:
                model, cores, flags = _parse_cpuinfo(text)
                out.update(cpu_model=model, cpu_cores=cores, cpu_flags=flags)
                return
        elif os_name == "windows" and env.windows_cpu is not None:
            model, flags = env.windows_cpu()
            out.update(cpu_model=model, cpu_flags=flags)
            return
        elif os_name == "darwin":
            res = env.run(["sysctl", "-n", "machdep.cpu.brand_string"], 5)
            if res and res.returncode == 0:
                out["cpu_model"] = res.stdout.strip() or None
            return
        notes.append("CPU details not available")

    def _gpus(
        self, os_name: str, out: dict[str, object], notes: list[str]
    ) -> tuple[list[GpuInfo], bool]:
        if os_name == "darwin" and out.get("arch") == "arm64":
            return [
                GpuInfo(vendor=GpuVendor.APPLE, name="Apple Silicon GPU", unified_memory=True)
            ], True
        res = self._env.run(_NVIDIA_QUERY, 15)
        if res is None:
            notes.append(
                "nvidia-smi not found: NVIDIA GPUs without a driver, and AMD/Intel "
                "GPUs, are not detected"
            )
            return [], True
        if res.returncode != 0:
            notes.append("nvidia-smi failed: GPU state unknown")
            return [], False
        return parse_nvidia_smi(res.stdout), True

    def _disk(self, notes: list[str]) -> tuple[int | None, str | None]:
        path = self._data_dir.resolve()
        while not path.exists() and path.parent != path:
            path = path.parent
        try:
            return self._env.disk_free(path), str(path)
        except OSError:
            notes.append(f"could not read free disk space at {path}")
            return None, str(path)

    def _runtimes(self, os_name: str) -> list[RuntimeInfo]:
        env = self._env
        out = [self._ollama()]
        docker = env.run(["docker", "version", "--format", "{{.Server.Version}}"], 10)
        if docker is None:
            out.append(RuntimeInfo(name="docker", installed=False, reachable=False))
        else:
            ok = docker.returncode == 0 and bool(docker.stdout.strip())
            out.append(
                RuntimeInfo(
                    name="docker",
                    installed=True,
                    reachable=ok,
                    version=docker.stdout.strip() if ok else None,
                    detail=None if ok else "Docker CLI found but the engine did not answer",
                )
            )
        out.append(self._tesseract())
        if os_name == "windows":
            wsl = env.run(["wsl", "--status"], 10)
            out.append(
                RuntimeInfo(
                    name="wsl",
                    installed=None if wsl is None else wsl.returncode == 0,
                    detail=None
                    if wsl is None
                    else next(iter(wsl.stdout.strip().splitlines()), None),
                )
            )
        return out

    def _tesseract(self) -> RuntimeInfo:
        binary = find_tesseract(
            which=lambda name: name if self._env.run([name, "--version"], 10) else None,
            candidates=[Path(p) for p in self._env.tesseract_paths],
        )
        if binary is None:
            return RuntimeInfo(name="tesseract", installed=False)
        ver = self._env.run([binary, "--version"], 10)
        langs = self._env.run([binary, "--list-langs"], 10)
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", ver.stdout) if ver else None
        found = sorted(parse_langs(langs.stdout)) if langs else []
        return RuntimeInfo(
            name="tesseract",
            installed=True,
            version=m.group(1) if m else None,
            detail=f"languages: {', '.join(found) or 'none'}",
        )

    def _ollama(self) -> RuntimeInfo:
        env = self._env
        res = env.run(["ollama", "--version"], 10)
        if res is None:
            for candidate in env.ollama_paths:
                if candidate and Path(candidate).is_file():
                    res = env.run([candidate, "--version"], 10)
                    break
        version = None
        if res is not None and res.returncode == 0:
            m = re.search(r"(\d+\.\d+\.\d+)", res.stdout)
            version = m.group(1) if m else None
        status = env.http(f"{self._ollama_url}/api/version", 3)
        return RuntimeInfo(
            name="ollama",
            installed=res is not None or status == 200,
            version=version,
            reachable=status == 200,
            detail=None if status == 200 else "service not answering on its local API",
        )
