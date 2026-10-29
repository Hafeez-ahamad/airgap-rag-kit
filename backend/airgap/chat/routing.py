"""Model routing hook. Phase 1 serves every role from one model; Phase 2b adds
rule-based routes from YAML (spec §4.7) behind the same interface."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from airgap.core.ports import LLMBackend
from airgap.core.types import Mode


class PipelineRole(StrEnum):
    GROUNDED_ANSWER = "grounded_answer"
    GENERAL_CHAT = "general_chat"
    QUERY_REWRITE = "query_rewrite"
    INTENT_CLASSIFY = "intent_classify"
    TITLE_SUMMARY = "title_summary"


class UnsupportedMode(ValueError):
    pass


def answer_role(mode: Mode) -> PipelineRole:
    if mode is Mode.GROUNDED:
        return PipelineRole.GROUNDED_ANSWER
    if mode in (Mode.GENERAL, Mode.ASSISTANT):
        return PipelineRole.GENERAL_CHAT
    raise UnsupportedMode(f"mode '{mode.value}' is not available in this version")


class Router(Protocol):
    def backend_for(self, role: PipelineRole) -> LLMBackend: ...


class SingleModelRouter:
    def __init__(self, backend: LLMBackend) -> None:
        self._backend = backend

    def backend_for(self, role: PipelineRole) -> LLMBackend:
        return self._backend
