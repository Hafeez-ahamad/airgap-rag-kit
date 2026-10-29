"""Admin: audit log query/export and redacted diagnostics."""

from __future__ import annotations

import platform
import sys
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from airgap import __version__
from airgap.api.deps import AdminDep, ServicesDep
from airgap.auth.audit import AuditFilter
from airgap.redact import redact_mapping

router = APIRouter(tags=["admin"])


def _filter(
    actor: str | None,
    action: str | None,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> AuditFilter:
    return AuditFilter(actor=actor, action=action, since=since, until=until, limit=limit)


@router.get("/admin/audit")
def audit(
    services: ServicesDep,
    _admin: AdminDep,
    actor: str | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=200, ge=1, le=5000),
) -> list[dict[str, Any]]:
    if services.audit is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "audit log not configured")
    return services.audit.query(_filter(actor, action, since, until, limit))


@router.get("/admin/audit/export")
def audit_export(
    services: ServicesDep,
    admin: AdminDep,
    actor: str | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=5000, ge=1, le=5000),
) -> StreamingResponse:
    if services.audit is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "audit log not configured")
    services.audit.record(admin.username, "audit.export")
    return StreamingResponse(
        services.audit.export_jsonl(_filter(actor, action, since, until, limit)),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=airgap-audit.jsonl"},
    )


@router.get("/admin/diagnostics")
def diagnostics(services: ServicesDep, _admin: AdminDep) -> dict[str, Any]:
    """Safe, redacted support bundle: versions, component health, setup progress. No secrets,
    no document text, no raw tracebacks."""
    components: dict[str, str] = {}
    for name, check in services.health_checks.items():
        try:
            components[name] = "ok" if check() else "down"
        except Exception as exc:
            components[name] = f"down ({type(exc).__name__})"
    setup: dict[str, Any] | None = None
    if services.setup is not None:
        state = services.setup.store.load()
        setup = {
            "complete": state.complete,
            "steps": {k.value: v.model_dump(mode="json") for k, v in state.steps.items()},
            "active_model_id": state.active_model_id,
            "index_version": state.index_version,
            "recent_events": state.events[-30:],
            "tier": state.assessment.tier.value if state.assessment else None,
        }
    s = services.settings
    return redact_mapping(
        {
            "app_version": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(terse=True),
            "profile": s.profile,
            "components": components,
            "setup": setup,
            "settings": {
                "llm_model": s.llm_model,
                "llm_num_ctx": s.llm_num_ctx,
                "confidence_threshold": s.confidence_threshold,
                "confidence_threshold_calibrated": False,
                "max_upload_bytes": s.max_upload_bytes,
                "dev_auth": s.dev_auth,
                "bind_note": "the native profile listens on 127.0.0.1 unless configured otherwise",
            },
        }
    )
