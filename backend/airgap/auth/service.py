"""Local accounts, sessions, roles (ADR-0010).

Roles:
  viewer: chat (both assistants), read documents in their groups, own history and feedback.
  editor: viewer + upload / re-upload / delete documents and set ACLs within their groups.
  admin:  everything, including users, setup, models, audit log and the Compatibility Lab.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from airgap.core.ports import Clock
from airgap.core.types import DEFAULT_GROUP
from airgap.db.models import AuthSession, User, new_id


class UserRole(StrEnum):
    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"


ROLE_RANK = {UserRole.VIEWER: 0, UserRole.EDITOR: 1, UserRole.ADMIN: 2}
USERNAME = re.compile(r"^[a-z0-9_.-]{3,64}$")
GROUP = re.compile(r"^[a-z0-9_-]{1,64}$")
MIN_PASSWORD = 12
MAX_FAILED = 5
LOCKOUT = timedelta(minutes=15)
SESSION_TTL = timedelta(hours=12)


class AuthError(ValueError):
    """Login failed. The message never says whether the user exists."""


class ValidationFailed(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Principal:
    id: str
    username: str
    role: UserRole
    groups: tuple[str, ...]

    def at_least(self, role: UserRole) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]


@dataclass(frozen=True, slots=True)
class NewSession:
    token: str  # goes into the HttpOnly cookie; only its hash is stored
    csrf_token: str
    principal: Principal
    expires_at: datetime


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _principal(user: User) -> Principal:
    return Principal(user.id, user.username, UserRole(user.role), tuple(user.groups))


def validate_groups(groups: list[str]) -> list[str]:
    cleaned = sorted({g.strip().lower() for g in groups})
    if not cleaned or not all(GROUP.match(g) for g in cleaned):
        raise ValidationFailed("groups must be 1-64 chars of a-z, 0-9, '_' or '-'")
    return cleaned


def _validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValidationFailed(f"password must be at least {MIN_PASSWORD} characters")
    if len(password) > 1024:
        raise ValidationFailed("password too long")


class AuthService:
    def __init__(
        self, sessions: sessionmaker[Session], clock: Clock, hasher: PasswordHasher | None = None
    ) -> None:
        self._sessions, self._clock = sessions, clock
        self._hasher = hasher or PasswordHasher()
        # Constant-time-ish path for unknown users: verify against a dummy hash.
        self._dummy = self._hasher.hash(secrets.token_urlsafe(16))

    def user_count(self) -> int:
        with self._sessions() as db:
            return int(db.scalar(select(func.count()).select_from(User)) or 0)

    def create_user(
        self, username: str, password: str, role: UserRole, groups: list[str] | None = None
    ) -> Principal:
        username = username.strip().lower()
        if not USERNAME.match(username):
            raise ValidationFailed("username must be 3-64 chars of a-z, 0-9, '.', '_' or '-'")
        _validate_password(password)
        clean_groups = validate_groups(groups or [DEFAULT_GROUP])
        with self._sessions.begin() as db:
            if db.scalar(select(User).where(User.username == username)) is not None:
                raise ValidationFailed("username already exists")
            user = User(
                id=new_id(),
                username=username,
                password_hash=self._hasher.hash(password),
                role=role.value,
                groups=clean_groups,
                created_at=self._clock.now(),
            )
            db.add(user)
            return _principal(user)

    def list_users(self) -> list[tuple[Principal, bool]]:
        with self._sessions() as db:
            users = db.scalars(select(User).order_by(User.username)).all()
            return [(_principal(u), u.disabled) for u in users]

    def update_user(
        self,
        user_id: str,
        *,
        role: UserRole | None = None,
        groups: list[str] | None = None,
        disabled: bool | None = None,
        password: str | None = None,
    ) -> Principal:
        with self._sessions.begin() as db:
            user = db.get(User, user_id)
            if user is None:
                raise ValidationFailed("user not found")
            if role is not None:
                user.role = role.value
            if groups is not None:
                user.groups = validate_groups(groups)
            if disabled is not None:
                user.disabled = disabled
            if password is not None:
                _validate_password(password)
                user.password_hash = self._hasher.hash(password)
                user.failed_logins, user.locked_until = 0, None
            if disabled or password is not None or role is not None or groups is not None:
                db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
            admins = db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == UserRole.ADMIN.value, User.disabled.is_(False))
            )
            if not admins:
                raise ValidationFailed("at least one enabled admin must remain")
            return _principal(user)

    def login(self, username: str, password: str) -> NewSession:
        now = self._clock.now()
        failure: str | None = None
        with self._sessions.begin() as db:
            user = db.scalar(select(User).where(User.username == username.strip().lower()))
            if user is None:
                self._verify(self._dummy, password)
                failure = "invalid username or password"
            elif user.disabled or (
                user.locked_until is not None and _aware(user.locked_until) > now
            ):
                self._verify(self._dummy, password)
                failure = "invalid username or password, or account locked"
            elif not self._verify(user.password_hash, password):
                # Committed (no raise inside the transaction), so lockout counts survive.
                user.failed_logins += 1
                if user.failed_logins >= MAX_FAILED:
                    user.locked_until = now + LOCKOUT
                    user.failed_logins = 0
                failure = "invalid username or password"
        if failure is not None:
            raise AuthError(failure)
        with self._sessions.begin() as db:
            user = db.scalar(select(User).where(User.username == username.strip().lower()))
            assert user is not None
            user.failed_logins, user.locked_until = 0, None
            if self._hasher.check_needs_rehash(user.password_hash):
                user.password_hash = self._hasher.hash(password)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            expires = now + SESSION_TTL
            db.add(
                AuthSession(
                    id=_hash_token(token),
                    user_id=user.id,
                    csrf_token=csrf,
                    created_at=now,
                    expires_at=expires,
                )
            )
            return NewSession(token, csrf, _principal(user), expires)

    def _verify(self, hashed: str, password: str) -> bool:
        try:
            return self._hasher.verify(hashed, password)
        except (VerificationError, InvalidHashError):
            return False

    def resolve(self, token: str) -> tuple[Principal, str] | None:
        """Session token -> (principal, csrf token), or None if missing/expired/disabled."""
        now = self._clock.now()
        with self._sessions.begin() as db:
            row = db.get(AuthSession, _hash_token(token))
            if row is None:
                return None
            if _aware(row.expires_at) <= now:
                db.delete(row)
                return None
            user = db.get(User, row.user_id)
            if user is None or user.disabled:
                return None
            return _principal(user), row.csrf_token

    def logout(self, token: str) -> None:
        with self._sessions.begin() as db:
            db.execute(delete(AuthSession).where(AuthSession.id == _hash_token(token)))


def _aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo; values are always written in UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
