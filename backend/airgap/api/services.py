"""Service container: what the routes depend on. Tests build it from fakes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from airgap.auth.limits import RateLimiter
from airgap.chat.history import ChatStore
from airgap.chat.orchestrator import Orchestrator
from airgap.config import Settings
from airgap.ingestion.documents import DocumentService

if TYPE_CHECKING:
    from airgap.auth.audit import AuditLog
    from airgap.auth.service import AuthService
    from airgap.setup.service import SetupService

HealthCheck = Callable[[], bool]


@dataclass
class Services:
    settings: Settings
    documents: DocumentService
    chat: ChatStore
    orchestrator: Orchestrator
    health_checks: dict[str, HealthCheck]
    auth: AuthService | None = None
    audit: AuditLog | None = None
    setup: SetupService | None = None
    chat_limiter: RateLimiter = field(
        default_factory=lambda: RateLimiter(per_minute=30, max_concurrent=2, global_concurrent=8)
    )
    # Per-client login throttle, on top of the per-account lockout in AuthService.
    login_limiter: RateLimiter = field(
        default_factory=lambda: RateLimiter(per_minute=10, max_concurrent=2)
    )
