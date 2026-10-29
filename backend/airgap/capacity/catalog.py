"""Model catalogue: typed, validated view of ``data/catalog.yaml``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

CATALOG_PATH = Path(__file__).with_name("data") / "catalog.yaml"

PERMISSIVE_LICENCES = frozenset({"apache-2.0", "mit", "bsd-3-clause"})


class Licence(BaseModel):
    id: str
    url: str | None = None
    commercial_use: bool | Literal["conditional"]
    redistribution: bool
    verified: str

    @property
    def allowed_by_default(self) -> bool:
        """Licence gate (REQUIREMENTS §4.5): permissive and commercial use allowed."""
        return self.id in PERMISSIVE_LICENCES and self.commercial_use is True


class Architecture(BaseModel):
    layers: int = Field(ge=1)
    kv_heads: int = Field(ge=1)
    head_dim: int = Field(ge=1)
    attention: Literal["full", "hybrid_sliding"] = "full"
    sliding_window: int | None = Field(default=None, ge=1)
    full_attention_layers: int | None = Field(default=None, ge=0)
    moe: bool = False
    source: str

    @model_validator(mode="after")
    def _hybrid_needs_window(self) -> Architecture:
        if self.attention == "hybrid_sliding":
            if self.sliding_window is None or self.full_attention_layers is None:
                raise ValueError("hybrid_sliding needs sliding_window and full_attention_layers")
            if self.full_attention_layers > self.layers:
                raise ValueError("full_attention_layers exceeds layers")
        return self


class Artifact(BaseModel):
    tag: str
    download_bytes: int = Field(gt=0)
    size_source: str
    digest: str | None = None


class LLMEntry(BaseModel):
    id: str
    display_name: str
    family: str
    params_b: float = Field(gt=0)
    quantization: str
    format: str
    artifacts: dict[str, Artifact]
    architecture: Architecture
    max_context: int = Field(ge=512)
    licence: Licence
    languages: list[str]
    capabilities: list[str]
    quality_rank: int | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def weights_bytes(self) -> int:
        """GGUF weights resident size ~ file size (estimate: the file is the tensors)."""
        return min(a.download_bytes for a in self.artifacts.values())


class Runtime(BaseModel):
    display_name: str
    licence: str
    tested_versions: list[str]
    min_version: str
    platforms: list[str]
    accelerators: list[str]
    host_overhead_bytes: int = Field(ge=0)
    gpu_overhead_bytes: int = Field(ge=0)
    kv_bytes_per_element: dict[str, float]
    install_url: str


class ModelFile(BaseModel):
    path: str
    bytes: int | None = None
    sha256: str | None = None


class RetrievalModel(BaseModel):
    name: str
    revision: str
    dim: int | None = None
    licence: Licence
    files: list[ModelFile]


class RetrievalProfile(BaseModel):
    id: str
    display_name: str
    index_version: str
    embedder: RetrievalModel
    reranker: RetrievalModel
    download_bytes: int
    dtype: str
    resident_bytes: int
    resident_source: str
    peak_bytes: int | None = None  # transient peak while loading, if measured
    peak_source: str | None = None

    @property
    def planning_bytes(self) -> int:
        """Budget for the worst moment (loading), not only steady state."""
        return max(self.resident_bytes, self.peak_bytes or 0)


class Catalog(BaseModel):
    schema_version: Literal[1]
    catalog_version: str
    runtimes: dict[str, Runtime]
    llms: list[LLMEntry]
    retrieval_profiles: list[RetrievalProfile] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Catalog:
        ids = [m.id for m in self.llms]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate model id in catalogue")
        for model in self.llms:
            unknown = set(model.artifacts) - set(self.runtimes)
            if unknown:
                raise ValueError(f"{model.id}: artifacts for unknown runtimes {sorted(unknown)}")
        return self

    def llm(self, model_id: str) -> LLMEntry:
        for model in self.llms:
            if model.id == model_id:
                return model
        raise KeyError(model_id)

    @property
    def retrieval(self) -> RetrievalProfile:
        """The platform-managed default; there is one profile today."""
        return self.retrieval_profiles[0]


def parse_catalog(text: str) -> Catalog:
    return Catalog.model_validate(yaml.safe_load(text))


@lru_cache(maxsize=1)
def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    return parse_catalog(path.read_text(encoding="utf-8"))
