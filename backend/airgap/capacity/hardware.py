"""Hardware report: what a probe (real or simulated) found, with its limits.

The same model is produced by the real probe and by the Compatibility Lab's simulated provider,
so the planner never knows (or cares) which one it got. ``source`` says which, and every
consumer must surface it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

GIB = 1024**3


class ReportSource(StrEnum):
    REAL = "real"
    SIMULATED = "simulated"


class GpuVendor(StrEnum):
    NVIDIA = "nvidia"
    AMD = "amd"
    APPLE = "apple"
    INTEL = "intel"
    UNKNOWN = "unknown"


class GpuInfo(BaseModel):
    vendor: GpuVendor = GpuVendor.UNKNOWN
    name: str = "unknown"
    vram_total_bytes: int | None = Field(default=None, ge=0)
    vram_free_bytes: int | None = Field(default=None, ge=0)
    driver_version: str | None = None
    compute_capability: str | None = None
    # True when GPU memory is the system RAM (Apple Silicon). vram_* are then unset.
    unified_memory: bool = False

    @property
    def usable_accelerator(self) -> str | None:
        """Accelerator the catalogue runtimes understand, or None if this GPU is unusable."""
        if self.vendor is GpuVendor.NVIDIA and self.driver_version:
            return "cuda"
        if self.vendor is GpuVendor.APPLE and self.unified_memory:
            return "metal"
        return None


class RuntimeInfo(BaseModel):
    name: str
    installed: bool | None = None  # None: could not tell
    version: str | None = None
    reachable: bool | None = None  # service answers on its API
    detail: str | None = None


class HardwareReport(BaseModel):
    source: ReportSource
    profile_name: str | None = None  # simulated profile id, if any
    os: Literal["windows", "linux", "darwin", "unknown"] = "unknown"
    os_version: str | None = None
    arch: Literal["amd64", "arm64", "unknown"] = "unknown"
    cpu_model: str | None = None
    cpu_cores: int | None = Field(default=None, ge=1)
    cpu_threads: int | None = Field(default=None, ge=1)
    cpu_flags: list[str] | None = None  # None: not inspected; [] would mean "none found"
    ram_total_bytes: int | None = Field(default=None, ge=0)
    ram_available_bytes: int | None = Field(default=None, ge=0)
    swap_total_bytes: int | None = Field(default=None, ge=0)
    disk_free_bytes: int | None = Field(default=None, ge=0)
    disk_path: str | None = None
    gpus: list[GpuInfo] = Field(default_factory=list)
    gpu_probe_ok: bool = False  # False: GPU detection could not run, so "no GPU" is unproven
    runtimes: list[RuntimeInfo] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalise(self) -> HardwareReport:
        """Repair contradictions conservatively and say so."""
        notes = self.limitations
        if (
            self.ram_total_bytes is not None
            and self.ram_available_bytes is not None
            and self.ram_available_bytes > self.ram_total_bytes
        ):
            notes.append("available RAM exceeded total RAM; available clamped to total")
            self.ram_available_bytes = self.ram_total_bytes
        for gpu in self.gpus:
            if (
                gpu.vram_total_bytes is not None
                and gpu.vram_free_bytes is not None
                and gpu.vram_free_bytes > gpu.vram_total_bytes
            ):
                notes.append(f"{gpu.name}: free VRAM exceeded total; clamped")
                gpu.vram_free_bytes = gpu.vram_total_bytes
            if gpu.unified_memory and gpu.vendor is not GpuVendor.APPLE:
                notes.append(f"{gpu.name}: unified memory is only modelled for Apple Silicon")
                gpu.unified_memory = False
        if self.cpu_cores and self.cpu_threads and self.cpu_threads < self.cpu_cores:
            notes.append("CPU threads below cores; threads set to cores")
            self.cpu_threads = self.cpu_cores
        if self.ram_total_bytes is None:
            notes.append("total RAM unknown: no model can be recommended safely")
        if self.disk_free_bytes is None:
            notes.append("free disk unknown: downloads cannot be checked")
        if not self.gpu_probe_ok:
            notes.append("GPU detection did not run or failed: planning as CPU-only")
        self.limitations = list(dict.fromkeys(notes))  # dedupe, keep order
        return self

    def runtime(self, name: str) -> RuntimeInfo | None:
        return next((r for r in self.runtimes if r.name == name), None)

    @property
    def platform(self) -> str:
        return f"{self.os}/{self.arch}"
