"""Authentication and user administration (ADR-0010)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from airgap.api.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AdminDep,
    ServicesDep,
    UserDep,
    is_loopback,
)
from airgap.api.services import Services
from airgap.auth.limits import LimitExceeded
from airgap.auth.service import (
    SESSION_TTL,
    AuthError,
    AuthService,
    NewSession,
    UserRole,
    ValidationFailed,
)

router = APIRouter(tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class SessionOut(BaseModel):
    username: str
    role: UserRole
    groups: list[str]
    csrf_token: str


class MeOut(BaseModel):
    id: str
    username: str
    role: UserRole
    groups: list[str]
    dev_auth: bool


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=1024)
    role: UserRole = UserRole.VIEWER
    groups: list[str] = Field(default_factory=lambda: ["default"], max_length=32)


class UserPatch(BaseModel):
    role: UserRole | None = None
    groups: list[str] | None = Field(default=None, max_length=32)
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=1024)


class UserOut(BaseModel):
    id: str
    username: str
    role: UserRole
    groups: list[str]
    disabled: bool


def _auth(services: Services) -> AuthService:
    if services.auth is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "authentication not configured")
    return services.auth


def _set_cookies(response: Response, services: Services, session: NewSession) -> SessionOut:
    secure = services.settings.cookie_secure
    max_age = int(SESSION_TTL.total_seconds())
    response.set_cookie(
        SESSION_COOKIE,
        session.token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    # Readable by the UI so it can echo it in X-CSRF-Token (double submit).
    response.set_cookie(
        CSRF_COOKIE,
        session.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )
    p = session.principal
    return SessionOut(
        username=p.username, role=p.role, groups=list(p.groups), csrf_token=session.csrf_token
    )


def _audit(services: Services, actor: str, action: str, outcome: str, **detail: object) -> None:
    if services.audit is not None:
        services.audit.record(actor, action, outcome=outcome, detail=dict(detail))


@router.get("/auth/status")
def auth_status(services: ServicesDep) -> dict[str, bool]:
    """Public: lets the UI decide between login, first-run admin creation and dev mode."""
    needs_bootstrap = services.auth is not None and services.auth.user_count() == 0
    return {"dev_auth": services.settings.dev_auth, "needs_bootstrap": needs_bootstrap}


@router.post("/auth/bootstrap", response_model=SessionOut, status_code=201)
def bootstrap(
    body: Credentials, request: Request, response: Response, services: ServicesDep
) -> SessionOut:
    """Create the first admin. Only while no account exists, and only from this machine."""
    auth = _auth(services)
    if not is_loopback(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "first admin must be created locally")
    if auth.user_count() > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "an admin already exists")
    try:
        auth.create_user(body.username, body.password, UserRole.ADMIN, ["default"])
    except ValidationFailed as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    _audit(services, body.username.lower(), "auth.bootstrap", "ok")
    return _set_cookies(response, services, auth.login(body.username, body.password))


@router.post("/auth/login", response_model=SessionOut)
def login(
    body: Credentials, request: Request, response: Response, services: ServicesDep
) -> SessionOut:
    auth = _auth(services)
    client = request.client.host if request.client else "unknown"
    try:
        with services.login_limiter.acquire(client):
            session = auth.login(body.username, body.password)
    except LimitExceeded as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            str(exc),
            headers={"Retry-After": str(exc.retry_after_s)},
        ) from exc
    except AuthError as exc:
        _audit(services, body.username.lower()[:64], "auth.login", "denied")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    _audit(services, session.principal.username, "auth.login", "ok")
    return _set_cookies(response, services, session)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, services: ServicesDep, user: UserDep) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token and services.auth is not None:
        services.auth.logout(token)
    _audit(services, user.username or user.id, "auth.logout", "ok")
    response.status_code = 204
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


@router.get("/auth/me", response_model=MeOut)
def me(user: UserDep, services: ServicesDep) -> MeOut:
    return MeOut(
        id=user.id,
        username=user.username or user.id,
        role=user.role,
        groups=list(user.groups),
        dev_auth=services.settings.dev_auth,
    )


@router.get("/admin/users", response_model=list[UserOut])
def list_users(services: ServicesDep, _admin: AdminDep) -> list[UserOut]:
    return [
        UserOut(id=p.id, username=p.username, role=p.role, groups=list(p.groups), disabled=disabled)
        for p, disabled in _auth(services).list_users()
    ]


@router.post("/admin/users", response_model=UserOut, status_code=201)
def create_user(body: UserIn, services: ServicesDep, admin: AdminDep) -> UserOut:
    try:
        p = _auth(services).create_user(body.username, body.password, body.role, body.groups)
    except ValidationFailed as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    _audit(services, admin.username, "user.create", "ok", user=p.username, role=p.role.value)
    return UserOut(id=p.id, username=p.username, role=p.role, groups=list(p.groups), disabled=False)


@router.patch("/admin/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserPatch, services: ServicesDep, admin: AdminDep) -> UserOut:
    try:
        p = _auth(services).update_user(
            user_id,
            role=body.role,
            groups=body.groups,
            disabled=body.disabled,
            password=body.password,
        )
    except ValidationFailed as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    changed = [k for k, v in body.model_dump().items() if v is not None]
    _audit(services, admin.username, "user.update", "ok", user=p.username, fields=changed)
    disabled = {u.id: d for u, d in _auth(services).list_users()}[p.id]
    return UserOut(
        id=p.id, username=p.username, role=p.role, groups=list(p.groups), disabled=disabled
    )
