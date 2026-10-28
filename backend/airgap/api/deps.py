"""Request dependencies. ``current_user`` is the single place auth is decided (ADR-0008)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from airgap.api.services import Services
from airgap.core.types import DEFAULT_GROUP

_DEV_USER = re.compile(r"^[a-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True)
class User:
    id: str
    groups: tuple[str, ...]


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


def current_user(services: ServicesDep, x_dev_user: Annotated[str | None, Header()] = None) -> User:
    if not services.settings.dev_auth:
        # Real authentication arrives in Phase 3; until then nothing is open by default.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    user_id = (x_dev_user or "dev").strip().lower()
    if not _DEV_USER.match(user_id):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid dev user")
    return User(user_id, (DEFAULT_GROUP,))


UserDep = Annotated[User, Depends(current_user)]
