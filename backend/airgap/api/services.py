"""Service container: what the routes depend on. Tests build it from fakes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from airgap.chat.history import ChatStore
from airgap.chat.orchestrator import Orchestrator
from airgap.config import Settings
from airgap.ingestion.documents import DocumentService

HealthCheck = Callable[[], bool]


@dataclass
class Services:
    settings: Settings
    documents: DocumentService
    chat: ChatStore
    orchestrator: Orchestrator
    health_checks: dict[str, HealthCheck]
