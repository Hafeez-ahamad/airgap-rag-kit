"""Hardware providers. The planner takes a ``HardwareReport`` from any of these.

``SimulatedHardwareProvider`` exists for the Compatibility Lab and tests. Its reports are
always marked ``source=simulated``, and nothing that installs or runs models may accept them.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol

import yaml
from pydantic import BaseModel, Field

from airgap.capacity.hardware import (
    GIB,
    GpuInfo,
    GpuVendor,
    HardwareReport,
    ReportSource,
    RuntimeInfo,
)

PROFILES_PATH = Path(__file__).with_name("data") / "simulated_profiles.yaml"
_MAX_GIB = 4096.0  # 4 TiB: anything larger in a custom spec is a typo


class HardwareProvider(Protocol):
    def report(self) -> HardwareReport: ...


def _bytes(gib: float | None) -> int | None:
    return None if gib is None else int(gib * GIB)


class SimulatedGpu(BaseModel):
    vendor: GpuVendor = GpuVendor.NVIDIA
    name: str = Field(default="Simulated GPU", max_length=120)
    vram_total_gib: float | None = Field(default=None, ge=0, le=_MAX_GIB)
    vram_free_gib: float | None = Field(default=None, ge=0, le=_MAX_GIB)
    driver_version: str | None = Field(default=None, max_length=40)
    compute_capability: str | None = Field(default=None, max_length=10)
    unified_memory: bool = False


class SimulatedRuntime(BaseModel):
    name: str = Field(max_length=40)
    installed: bool | None = None
    version: str | None = Field(default=None, max_length=40)
    reachable: bool | None = None


class SimulatedSpec(BaseModel):
    """A synthetic device. Also the request body for custom specs in the Compatibility Lab."""

    id: str = Field(default="custom", max_length=64, pattern=r"^[a-z0-9-]+$")
    label: str = Field(default="Custom synthetic device", max_length=120)
    description: str = Field(default="", max_length=400)
    os: Literal["windows", "linux", "darwin", "unknown"] = "unknown"
    arch: Literal["amd64", "arm64", "unknown"] = "unknown"
    cpu_model: str | None = Field(default=None, max_length=120)
    cpu_cores: int | None = Field(default=None, ge=1, le=1024)
    cpu_threads: int | None = Field(default=None, ge=1, le=4096)
    cpu_flags: list[str] | None = Field(default=None, max_length=64)
    ram_total_gib: float | None = Field(default=None, ge=0, le=_MAX_GIB)
    ram_available_gib: float | None = Field(default=None, ge=0, le=_MAX_GIB)
    disk_free_gib: float | None = Field(default=None, ge=0, le=1024 * 1024)
    gpu_probe_ok: bool = True
    gpus: list[SimulatedGpu] = Field(default_factory=list, max_length=16)
    runtimes: list[SimulatedRuntime] = Field(default_factory=list, max_length=16)

    def to_report(self) -> HardwareReport:
        return HardwareReport(
            source=ReportSource.SIMULATED,
            profile_name=self.id,
            os=self.os,
            arch=self.arch,
            cpu_model=self.cpu_model,
            cpu_cores=self.cpu_cores,
            cpu_threads=self.cpu_threads,
            cpu_flags=self.cpu_flags,
            ram_total_bytes=_bytes(self.ram_total_gib),
            ram_available_bytes=_bytes(self.ram_available_gib),
            disk_free_bytes=_bytes(self.disk_free_gib),
            disk_path="(simulated)",
            gpu_probe_ok=self.gpu_probe_ok,
            gpus=[
                GpuInfo(
                    vendor=g.vendor,
                    name=g.name,
                    vram_total_bytes=_bytes(g.vram_total_gib),
                    vram_free_bytes=_bytes(g.vram_free_gib),
                    driver_version=g.driver_version,
                    compute_capability=g.compute_capability,
                    unified_memory=g.unified_memory,
                )
                for g in self.gpus
            ],
            runtimes=[RuntimeInfo(**r.model_dump()) for r in self.runtimes],
        )


class SimulatedHardwareProvider:
    def __init__(self, spec: SimulatedSpec) -> None:
        self._spec = spec

    def report(self) -> HardwareReport:
        return self._spec.to_report()


class _ProfileFile(BaseModel):
    profiles: list[SimulatedSpec]


@lru_cache(maxsize=1)
def builtin_profiles(path: Path = PROFILES_PATH) -> dict[str, SimulatedSpec]:
    data = _ProfileFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    return {p.id: p for p in data.profiles}
