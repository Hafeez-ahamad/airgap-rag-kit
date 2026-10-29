"""Application factory."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from airgap import __version__
from airgap.api.deps import ServicesDep
from airgap.api.routes_admin import router as admin_router
from airgap.api.routes_auth import router as auth_router
from airgap.api.routes_chat import router as chat_router
from airgap.api.routes_documents import router as documents_router
from airgap.api.routes_setup import router as setup_router
from airgap.api.services import Services

log = logging.getLogger(__name__)


def create_app(services: Services) -> FastAPI:
    app = FastAPI(
        title="AirGap RAG Kit",
        version=__version__,
        # Swagger UI loads assets from a CDN; serve only the JSON schema (no external calls).
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.services = services
    app.include_router(auth_router)
    app.include_router(documents_router)
    app.include_router(chat_router)
    app.include_router(setup_router)
    app.include_router(admin_router)

    @app.get("/health", tags=["health"])
    def health(svc: ServicesDep) -> dict[str, Any]:
        components: dict[str, str] = {}
        for name, check in svc.health_checks.items():
            try:
                components[name] = "ok" if check() else "down"
            except Exception:
                log.exception("health check failed", extra={"component": name})
                components[name] = "down"
        degraded = any(v != "ok" for v in components.values())
        setup_complete = None
        if svc.setup is not None:
            setup_complete = svc.setup.store.load().complete
        return {
            "status": "degraded" if degraded else "ok",
            "version": __version__,
            "components": components,
            "setup_complete": setup_complete,
        }

    return app
