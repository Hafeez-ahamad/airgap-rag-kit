"""POST /chat (SSE stream), GET /chat/history, POST /feedback."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from airgap.api.deps import ServicesDep, UserDep
from airgap.chat.history import NotFound
from airgap.chat.orchestrator import AnswerResult
from airgap.chat.routing import UnsupportedMode
from airgap.core.types import Mode

log = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])

_MAX_QUESTION_CHARS = 4000


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=_MAX_QUESTION_CHARS)
    mode: Mode = Mode.GROUNDED
    conversation_id: str | None = None


class FeedbackRequest(BaseModel):
    message_id: str
    rating: Literal["up", "down", "wrong"]
    comment: str | None = Field(default=None, max_length=2000)


class ConversationOut(BaseModel):
    id: str
    title: str
    updated_at: datetime


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    mode: str | None
    refused: bool
    flagged: bool
    citations: list[dict[str, Any]]
    segments: list[dict[str, Any]]
    created_at: datetime


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _final_payload(result: AnswerResult, conversation_id: str, message_id: str) -> dict[str, Any]:
    return {
        "type": "final",
        "conversation_id": conversation_id,
        "message_id": message_id,
        "answer": result.answer,
        "mode": result.mode.value,
        "refused": result.refused,
        "flagged": result.flagged,
        "model_id": result.model_id,
        "citations": result.citations_payload(),
        "segments": result.segments_payload(),
    }


@router.post("/chat")
def chat(req: ChatRequest, services: ServicesDep, user: UserDep) -> StreamingResponse:
    if req.mode is Mode.CODING:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "coding mode is not available yet")
    try:
        history = services.chat.history(user.id, req.conversation_id)
        conversation_id = services.chat.ensure_conversation(
            user.id, req.conversation_id, req.question
        )
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found") from exc

    def stream() -> Iterator[str]:
        try:
            for event in services.orchestrator.run(req.question, req.mode, history, user.groups):
                if event["type"] == "final":
                    result: AnswerResult = event["result"]
                    turn = services.chat.record(user.id, conversation_id, result)
                    yield _sse(_final_payload(result, conversation_id, turn.message_id))
                else:
                    yield _sse(event)
        except UnsupportedMode as exc:
            yield _sse({"type": "error", "message": str(exc)})
        except Exception:
            log.exception("chat failed")
            yield _sse({"type": "error", "message": "the assistant is unavailable, try again"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/history", response_model=list[ConversationOut] | list[MessageOut])
def chat_history(
    services: ServicesDep,
    user: UserDep,
    conversation_id: str | None = Query(default=None),
) -> list[ConversationOut] | list[MessageOut]:
    if conversation_id is None:
        return [
            ConversationOut(id=c.id, title=c.title, updated_at=c.updated_at)
            for c in services.chat.conversations(user.id)
        ]
    try:
        messages = services.chat.messages(user.id, conversation_id)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found") from exc
    return [
        MessageOut(
            id=m.id,
            role=m.role,
            content=m.content,
            mode=m.mode,
            refused=m.refused,
            flagged=m.flagged,
            citations=list(m.citations),
            segments=list(m.segments),
            created_at=m.created_at,
        )
        for m in messages
    ]


@router.post("/feedback", status_code=status.HTTP_201_CREATED)
def feedback(req: FeedbackRequest, services: ServicesDep, user: UserDep) -> dict[str, str]:
    try:
        fb = services.chat.add_feedback(user.id, req.message_id, req.rating, req.comment)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "message not found") from exc
    return {"id": fb.id}
