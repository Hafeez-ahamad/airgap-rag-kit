"""Audit log (spec F14). Insert and read only; the table itself rejects UPDATE/DELETE.

By default questions are stored as a SHA-256 digest plus length, not in clear, and document
text is never stored. ``store_questions=True`` keeps the question text (an explicit choice).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from airgap.core.ports import Clock
from airgap.db.models import AuditEvent
from airgap.redact import redact_mapping


@dataclass(frozen=True, slots=True)
class AuditFilter:
    actor: str | None = None
    action: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 200


class AuditLog:
    def __init__(
        self, sessions: sessionmaker[Session], clock: Clock, *, store_questions: bool = False
    ) -> None:
        self._sessions, self._clock, self._store_questions = sessions, clock, store_questions

    def question_detail(self, question: str) -> dict[str, Any]:
        if self._store_questions:
            return {"question": question}
        digest = hashlib.sha256(question.encode()).hexdigest()[:16]
        return {"question_sha256_16": digest, "question_chars": len(question)}

    def record(
        self,
        actor: str,
        action: str,
        *,
        target: str | None = None,
        outcome: str = "ok",
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._sessions.begin() as db:
            db.add(
                AuditEvent(
                    at=self._clock.now(),
                    actor=actor[:128],
                    action=action[:64],
                    target=None if target is None else target[:256],
                    outcome=outcome,
                    detail=redact_mapping(detail or {}),
                )
            )

    def query(self, f: AuditFilter) -> list[dict[str, Any]]:
        stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(f.limit, 5000))
        if f.actor:
            stmt = stmt.where(AuditEvent.actor == f.actor)
        if f.action:
            stmt = stmt.where(AuditEvent.action == f.action)
        if f.since:
            stmt = stmt.where(AuditEvent.at >= f.since)
        if f.until:
            stmt = stmt.where(AuditEvent.at <= f.until)
        with self._sessions() as db:
            return [_row(e) for e in db.scalars(stmt).all()]

    def export_jsonl(self, f: AuditFilter) -> Iterator[str]:
        for row in self.query(f):
            yield json.dumps(row, ensure_ascii=False) + "\n"


def _row(e: AuditEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "at": e.at.isoformat(),
        "actor": e.actor,
        "action": e.action,
        "target": e.target,
        "outcome": e.outcome,
        "detail": e.detail,
    }
