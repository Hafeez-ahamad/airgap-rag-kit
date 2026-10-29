"""Plain domain types. No I/O, no framework imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"


@dataclass(frozen=True, slots=True)
class Block:
    """One structural unit produced by a parser."""

    kind: BlockKind
    text: str
    page: int | None = None
    level: int = 0  # heading depth (1 = top); 0 for non-headings


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    blocks: list[Block]
    ocr_pages: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Chunk:
    """A retrievable unit of text plus the metadata needed for citations and ACLs."""

    chunk_id: str
    doc_id: str
    doc_name: str
    version: int
    index: int
    text: str
    page: int | None
    page_end: int | None
    section: str
    language: str
    acl_groups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    chunk: Chunk
    score: float


class DocStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


class Mode(StrEnum):
    # General Assistant: plain local-LLM chat. No retrieval, so no document grounding is implied.
    ASSISTANT = "assistant"
    # Document Assistant, strict: answers only from documents, cite or refuse.
    GROUNDED = "grounded"
    # Document Assistant, relaxed: documents when relevant, other content labelled.
    GENERAL = "general"
    # v2: text-only coding assistant. Accepted by the enum so routing has a hook,
    # rejected by the API until implemented.
    CODING = "coding"


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str


DEFAULT_GROUP = "default"
