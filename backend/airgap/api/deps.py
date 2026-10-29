"""Request dependencies. ``current_user`` is the single place authentication is decided.

Order: dev stub (development only, ADR-0008) -> session cookie (ADR-0010). State-changing
requests made with a session cookie must carry the session's CSRF token in ``X-CSRF-Token``.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from airgap.api.services import Services
from airgap.auth.service import UserRole
from airgap.core.types import DEFAULT_GROUP

_DEV_USER = re.compile(r"^[a-z0-9_-]{1,64}$")
SESSION_COOKIE = "airgap_session"
CSRF_COOKIE = "airgap_csrf"
_SAFE = {"GET", "HEAD", "OPTIONS"}
_RANK = {UserRole.VIEWER: 0, UserRole.EDITOR: 1, UserRole.ADMIN: 2}


@dataclass(frozen=True, slots=True)
class User:
    id: str
    groups: tuple[str, ...]
    role: UserRole = UserRole.VIEWER
    username: str = ""

    def at_least(self, role: UserRole) -> bool:
        return _RANK[self.role] >= _RANK[role]


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


def _unauthorized(detail: str = "authentication required") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail)


def current_user(
    request: Request,
    services: ServicesDep,
    x_dev_user: Annotated[str | None, Header()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> User:
    if services.settings.dev_auth:
        user_id = (x_dev_user or "dev").strip().lower()
        if not _DEV_USER.match(user_id):
            raise _unauthorized("invalid dev user")
        # Dev stub: every stub user is an admin in the default group (never in production).
        return User(user_id, (DEFAULT_GROUP,), UserRole.ADMIN, user_id)
    token = request.cookies.get(SESSION_COOKIE)
    if not token or services.auth is None:
        raise _unauthorized()
    resolved = services.auth.resolve(token)
    if resolved is None:
        raise _unauthorized("session expired")
    principal, csrf = resolved
    if request.method not in _SAFE and not (
        x_csrf_token and secrets.compare_digest(x_csrf_token, csrf)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing or invalid CSRF token")
    return User(principal.id, principal.groups, principal.role, principal.username)


UserDep = Annotated[User, Depends(current_user)]


def require(role: UserRole):  # type: ignore[no-untyped-def]
    def dep(user: UserDep) -> User:
        if not user.at_least(role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires the {role.value} role")
        return user

    return dep


EditorDep = Annotated[User, Depends(require(UserRole.EDITOR))]
AdminDep = Annotated[User, Depends(require(UserRole.ADMIN))]


def is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if host == "testclient":  # Starlette's TestClient
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def setup_access(
    request: Request,
    services: ServicesDep,
    x_dev_user: Annotated[str | None, Header()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> User:
    """First run (no accounts yet): open, but only from this machine. Afterwards: admins only."""
    if (
        not services.settings.dev_auth
        and services.auth is not None
        and services.auth.user_count() == 0
    ):
        if not is_loopback(request):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "first-run setup is local-only")
        return User("setup", (DEFAULT_GROUP,), UserRole.ADMIN, "setup")
    user = current_user(request, services, x_dev_user, x_csrf_token)
    if not user.at_least(UserRole.ADMIN):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "requires the admin role")
    return user


SetupDep = Annotated[User, Depends(setup_access)]
