"""The query flow from spec §7 as a generator of stream events.

Events (dicts, serialised as SSE by the API):
  {"type": "meta", "query": <standalone query>, "rewritten": bool}
  {"type": "token", "text": ...}
  {"type": "retry", "reason": ...}             # discard tokens streamed so far
  {"type": "final", "result": AnswerResult}
"""

from __future__ import annotations

from collections.abc import Generator, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from airgap.chat import prompts
from airgap.chat.routing import PipelineRole, Router, answer_role
from airgap.core.ports import GenerationOptions, LLMBackend
from airgap.core.types import ChatMessage, Chunk, Mode, Role
from airgap.retrieval.citations import Verification, verify
from airgap.retrieval.gate import REFUSAL_TEXT
from airgap.retrieval.retriever import HybridRetriever

GENERAL_LABEL = "general knowledge, not from your documents"
_MAX_REWRITE_CHARS = 500


@dataclass(frozen=True, slots=True)
class AnswerResult:
    mode: Mode
    question: str
    standalone_query: str
    answer: str  # display text, citations shown as [n]
    refused: bool
    flagged: bool  # citations still failed verification after one retry
    model_id: str | None
    best_score: float | None
    context: list[Chunk] = field(default_factory=list)
    verification: Verification | None = None

    def citations_payload(self) -> list[dict[str, Any]]:
        if self.verification is None:
            return []
        return [
            {
                "n": c.number,
                "quote": c.quote,
                "valid": c.valid,
                "reason": c.reason,
                "doc_id": c.chunk.doc_id if c.chunk else None,
                "doc_name": c.chunk.doc_name if c.chunk else None,
                "version": c.chunk.version if c.chunk else None,
                "page": c.chunk.page if c.chunk else None,
                "section": c.chunk.section if c.chunk else None,
                "chunk_id": c.chunk.chunk_id if c.chunk else None,
            }
            for c in self.verification.citations
        ]

    def segments_payload(self) -> list[dict[str, Any]]:
        if self.verification is None or self.mode is not Mode.GENERAL:
            return []
        return [
            {"text": s.text, "grounded": s.grounded, "label": None if s.grounded else GENERAL_LABEL}
            for s in self.verification.segments
        ]


class Orchestrator:
    def __init__(
        self, retriever: HybridRetriever, router: Router, options: GenerationOptions
    ) -> None:
        self._retriever, self._router, self._options = retriever, router, options

    def standalone_query(self, question: str, history: Sequence[ChatMessage]) -> str:
        if not any(m.role is Role.USER for m in history):
            return question
        backend = self._router.backend_for(PipelineRole.QUERY_REWRITE)
        rewritten = backend.complete(
            prompts.rewrite_messages(history, question),
            GenerationOptions(max_tokens=128, temperature=0.0, num_ctx=self._options.num_ctx),
        ).strip()
        rewritten = rewritten.strip('"').splitlines()[0].strip() if rewritten else ""
        if not rewritten or len(rewritten) > _MAX_REWRITE_CHARS:
            return question
        return rewritten

    def run(
        self,
        question: str,
        mode: Mode,
        history: Sequence[ChatMessage],
        acl_groups: Sequence[str],
    ) -> Iterator[dict[str, Any]]:
        role = answer_role(mode)  # raises UnsupportedMode for v2 modes
        if mode is Mode.ASSISTANT:
            yield from self._assistant(question, history)
            return
        query = self.standalone_query(question, history)
        yield {"type": "meta", "query": query, "rewritten": query != question}

        retrieval = self._retriever.retrieve(query, acl_groups)
        if mode is Mode.GROUNDED and not retrieval.gate.passed:
            yield {"type": "token", "text": REFUSAL_TEXT}
            yield {
                "type": "final",
                "result": AnswerResult(
                    mode,
                    question,
                    query,
                    REFUSAL_TEXT,
                    True,
                    False,
                    None,
                    retrieval.gate.best_score,
                ),
            }
            return

        context = [c.chunk for c in retrieval.reranked] if retrieval.gate.passed else []
        backend = self._router.backend_for(role)
        messages = prompts.answer_messages(mode, query, context, history)

        answer, verification = yield from self._generate(backend, messages, context)
        flagged = False
        if self._needs_retry(mode, verification, context):
            problems = [f"[{c.number}] {c.reason}" for c in verification.citations if not c.valid]
            if mode is Mode.GROUNDED and not verification.citations:
                problems.append("the answer had no citations")
            yield {"type": "retry", "reason": "; ".join(problems)}
            messages = [
                *messages,
                ChatMessage(Role.ASSISTANT, answer),
                prompts.retry_message(problems),
            ]
            answer, verification = yield from self._generate(backend, messages, context)
            flagged = self._needs_retry(mode, verification, context)

        yield {
            "type": "final",
            "result": AnswerResult(
                mode,
                question,
                query,
                verification.display_text,
                False,
                flagged,
                backend.model_id,
                retrieval.gate.best_score,
                context,
                verification,
            ),
        }

    def _assistant(self, question: str, history: Sequence[ChatMessage]) -> Iterator[dict[str, Any]]:
        """General Assistant: no retrieval, no citations, so nothing is presented as grounded."""
        yield {"type": "meta", "query": question, "rewritten": False}
        backend = self._router.backend_for(PipelineRole.GENERAL_CHAT)
        parts: list[str] = []
        for delta in backend.stream(prompts.assistant_messages(question, history), self._options):
            parts.append(delta)
            yield {"type": "token", "text": delta}
        answer = "".join(parts)
        yield {
            "type": "final",
            "result": AnswerResult(
                Mode.ASSISTANT, question, question, answer, False, False, backend.model_id, None
            ),
        }

    @staticmethod
    def _needs_retry(mode: Mode, v: Verification, context: Sequence[Chunk]) -> bool:
        if mode is Mode.GROUNDED:
            return not v.grounded
        return bool(context) and not v.all_valid  # general mode: only wrong citations

    def _generate(
        self, backend: LLMBackend, messages: list[ChatMessage], context: Sequence[Chunk]
    ) -> Generator[dict[str, Any], None, tuple[str, Verification]]:
        parts: list[str] = []
        for delta in backend.stream(messages, self._options):
            parts.append(delta)
            yield {"type": "token", "text": delta}
        answer = "".join(parts)
        return answer, verify(answer, context)
