"""Runtime settings, read from AIRGAP_* environment variables (and *_FILE secret files)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIRGAP_", extra="ignore")

    env: str = "development"  # "production" disables every dev shortcut
    # native: one process, SQLite + embedded Qdrant + in-process worker/models (ADR-0009).
    # compose: the multi-container stack (Postgres, Qdrant server, Valkey, model service).
    profile: Literal["native", "compose"] = "compose"
    data_dir: Path = Path("./data")
    models_dir: Path = Path("../models")
    host: str = "127.0.0.1"  # native profile: local-only unless explicitly configured
    port: int = 8000
    model_device: str = "cpu"  # in-process embedder/reranker (native profile)
    model_dtype: str = "bfloat16"
    dev_auth: bool = False  # Phase 1 stub user (ADR-0008); real auth is ADR-0010
    cookie_secure: bool = True  # session cookies only over HTTPS (localhost counts as secure)
    audit_store_questions: bool = False  # audit keeps a digest of questions unless enabled

    # May contain "{password}", filled from database_password_file (a compose secret).
    database_url: str = "sqlite:///./airgap-dev.db"
    database_password_file: Path | None = None
    redis_url: str = "redis://redis:6379/0"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "chunks"
    blob_root: Path = Path("./data/blobs")

    model_service_url: str = "http://models:8001"
    ollama_url: str = "http://ollama:11434"
    llm_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    llm_num_ctx: int = 4096

    max_upload_bytes: int = 50 * 1024 * 1024
    max_files_per_request: int = 200
    max_zip_uncompressed_bytes: int = 300 * 1024 * 1024
    ocr_languages: list[str] = Field(default_factory=lambda: ["eng", "hin"])
    ocr_min_text_chars: int = 25

    chunk_max_chars: int = 1200
    chunk_overlap_chars: int = 200

    retrieval_candidates: int = 30  # per search before fusion
    rerank_candidates: int = 20
    context_chunks: int = 5
    # Provisional and uncalibrated: calibrated per reranker on the eval set in Phase 3.
    confidence_threshold: float = 0.30
    tier_threshold_offset: float = 0.0

    @model_validator(mode="after")
    def _native_uses_local_services(self) -> Settings:
        """The compose defaults point at container names; the native profile talks to the host."""
        if self.profile == "native" and self.ollama_url == "http://ollama:11434":
            self.ollama_url = "http://127.0.0.1:11434"
        return self

    @model_validator(mode="after")
    def _no_dev_shortcuts_in_production(self) -> Settings:
        if self.env == "production" and self.dev_auth:
            raise ValueError("AIRGAP_DEV_AUTH must not be enabled when AIRGAP_ENV=production")
        return self

    @property
    def resolved_database_url(self) -> str:
        if "{password}" not in self.database_url:
            return self.database_url
        if self.database_password_file is None:
            raise ValueError("database_url has {password} but no database_password_file is set")
        password = self.database_password_file.read_text(encoding="utf-8").strip()
        return self.database_url.replace("{password}", quote(password, safe=""))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
