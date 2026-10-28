"""Per-user conversation history and feedback. Every query is scoped by user_id."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from airgap.chat.orchestrator import AnswerResult
from airgap.core.ports import Clock
from airgap.core.types import ChatMessage, Role
from airgap.db.models import Conversation, Feedback, Message, new_id

_HISTORY_TURNS = 10
_TITLE_CHARS = 80


class NotFound(LookupError):
    """Missing, or owned by another user (the two are indistinguishable to the caller)."""


@dataclass(frozen=True, slots=True)
class StoredTurn:
    conversation_id: str
    message_id: str


class ChatStore:
    def __init__(self, sessions: sessionmaker[Session], clock: Clock) -> None:
        self._sessions, self._clock = sessions, clock

    def _owned(self, db: Session, user_id: str, conversation_id: str) -> Conversation:
        conv = db.get(Conversation, conversation_id)
        if conv is None or conv.user_id != user_id:
            raise NotFound(conversation_id)
        return conv

    def history(self, user_id: str, conversation_id: str | None) -> list[ChatMessage]:
        if conversation_id is None:
            return []
        with self._sessions() as db:
            conv = self._owned(db, user_id, conversation_id)
            recent = conv.messages[-_HISTORY_TURNS:]
            return [ChatMessage(Role(m.role), m.content) for m in recent]

    def ensure_conversation(self, user_id: str, conversation_id: str | None, question: str) -> str:
        with self._sessions.begin() as db:
            if conversation_id is not None:
                return self._owned(db, user_id, conversation_id).id
            now = self._clock.now()
            conv = Conversation(
                id=new_id(),
                user_id=user_id,
                title=question[:_TITLE_CHARS],
                created_at=now,
                updated_at=now,
            )
            db.add(conv)
            return conv.id

    def record(self, user_id: str, conversation_id: str, result: AnswerResult) -> StoredTurn:
        now = self._clock.now()
        with self._sessions.begin() as db:
            conv = self._owned(db, user_id, conversation_id)
            conv.updated_at = now
            seq = len(conv.messages)
            db.add(
                Message(
                    conversation_id=conv.id,
                    user_id=user_id,
                    seq=seq,
                    role=Role.USER.value,
                    content=result.question,
                    created_at=now,
                )
            )
            answer = Message(
                id=new_id(),
                conversation_id=conv.id,
                user_id=user_id,
                seq=seq + 1,
                role=Role.ASSISTANT.value,
                content=result.answer,
                mode=result.mode.value,
                standalone_query=result.standalone_query,
                refused=result.refused,
                flagged=result.flagged,
                model_id=result.model_id,
                citations=result.citations_payload(),
                segments=result.segments_payload(),
                retrieved_chunk_ids=[c.chunk_id for c in result.context],
                created_at=now,
            )
            db.add(answer)
            return StoredTurn(conv.id, answer.id)

    def conversations(self, user_id: str) -> list[Conversation]:
        with self._sessions() as db:
            stmt = (
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.updated_at.desc())
            )
            return list(db.scalars(stmt))

    def messages(self, user_id: str, conversation_id: str) -> list[Message]:
        with self._sessions() as db:
            return list(self._owned(db, user_id, conversation_id).messages)

    def add_feedback(
        self, user_id: str, message_id: str, rating: str, comment: str | None
    ) -> Feedback:
        with self._sessions.begin() as db:
            msg = db.get(Message, message_id)
            if msg is None or msg.user_id != user_id or msg.role != Role.ASSISTANT.value:
                raise NotFound(message_id)
            siblings = msg.conversation.messages
            idx = next(i for i, m in enumerate(siblings) if m.id == msg.id)
            question = next(
                (m.content for m in reversed(siblings[:idx]) if m.role == Role.USER.value), ""
            )
            fb = Feedback(
                id=new_id(),
                user_id=user_id,
                message_id=msg.id,
                rating=rating,
                comment=comment,
                question=question,
                answer=msg.content,
                mode=msg.mode,
                retrieved_chunk_ids=list(msg.retrieved_chunk_ids),
                created_at=self._clock.now(),
            )
            db.add(fb)
            return fb
