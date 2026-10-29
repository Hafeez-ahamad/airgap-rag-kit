"""SQLAlchemy ORM models. Schema changes go through Alembic (backend/migrations)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}  # noqa: RUF012 - SQLAlchemy API


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(1024), unique=True)  # sanitised logical path
    file_type: Mapped[str] = mapped_column(String(16))
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    ocr_pages: Mapped[list[Any]] = mapped_column(default=list)
    acl_groups: Mapped[list[Any]] = mapped_column(default=list)
    uploaded_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentVersion.version"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    blob_key: Mapped[str] = mapped_column(String(2048))
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship(back_populates="versions")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.seq"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # position within the conversation
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    standalone_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    refused: Mapped[bool] = mapped_column(Boolean, default=False)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    citations: Mapped[list[Any]] = mapped_column(default=list)
    segments: Mapped[list[Any]] = mapped_column(default=list)
    retrieved_chunk_ids: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Feedback(Base):
    """Snapshot of question, answer and retrieved chunks, so it can be promoted into
    the eval set even if the conversation is later deleted."""

    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    message_id: Mapped[str] = mapped_column(String(36), index=True)
    rating: Mapped[str] = mapped_column(String(16))  # up | down | wrong
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    retrieved_chunk_ids: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class User(Base):
    """Local account (Phase 3 auth). Passwords are Argon2 hashes, never stored in clear."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16))  # admin | editor | viewer
    groups: Mapped[list[Any]] = mapped_column(default=list)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    """Server-side session. ``id`` is the SHA-256 of the cookie token (the token is not stored)."""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Append-only audit trail. Application code only ever inserts (no update/delete paths);
    on Postgres the app role is additionally denied UPDATE/DELETE (see migration 0002)."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(256), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16))  # ok | denied | failed
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
