"""Structure-aware chunking.

Rules:
- headings start a new chunk and set the section path ("A > B > C");
- a chunk never spans a page boundary (so a citation names one page);
- tables become their own chunks; oversized tables split by rows, repeating the header row;
- prose is packed up to ``max_chars``; when a chunk is split for size, the next chunk starts
  with up to ``overlap_chars`` of the previous one (cut at a word boundary).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from airgap.core.types import Block, BlockKind, Chunk
from airgap.ingestion.language import detect_language

_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")
_NAMESPACE = uuid.UUID("6f1c9d3e-2b8a-4c7e-9a51-0d3f2e7b8c41")


@dataclass(frozen=True, slots=True)
class DocMeta:
    doc_id: str
    doc_name: str
    version: int
    acl_groups: tuple[str, ...]


def chunk_id(doc_id: str, version: int, index: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{doc_id}:{version}:{index}"))


def _split_long(text: str, max_chars: int) -> list[str]:
    """Split one long paragraph at sentence boundaries, then at words if needed."""
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        if len(sentence) <= max_chars:
            pieces.append(sentence)
            continue
        words, cur = sentence.split(), ""
        for word in words:
            if cur and len(cur) + 1 + len(word) > max_chars:
                pieces.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}" if cur else word
        if cur:
            pieces.append(cur)
    return pieces


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return text if overlap > 0 else ""
    cut = text[-overlap:]
    space = cut.find(" ")
    return cut[space + 1 :] if 0 <= space < len(cut) - 1 else cut


def _split_table(text: str, max_chars: int) -> list[str]:
    rows = text.split("\n")
    if len(text) <= max_chars or len(rows) < 3:
        return [text]
    header, out, cur = rows[0], [], [rows[0]]
    for row in rows[1:]:
        if len(cur) > 1 and sum(len(r) + 1 for r in cur) + len(row) > max_chars:
            out.append("\n".join(cur))
            cur = [header]
        cur.append(row)
    if len(cur) > 1:
        out.append("\n".join(cur))
    return out


class _Builder:
    def __init__(self, meta: DocMeta, max_chars: int, overlap: int) -> None:
        self.meta, self.max_chars, self.overlap = meta, max_chars, overlap
        self.chunks: list[Chunk] = []
        self.section: list[tuple[int, str]] = []
        self.buf: list[str] = []
        self.page: int | None = None
        self.carry = ""  # overlap text pending for the next chunk

    @property
    def section_path(self) -> str:
        return " > ".join(t for _, t in self.section)

    def emit(self, text: str, page: int | None) -> None:
        text = text.strip()
        if not text:
            return
        index = len(self.chunks)
        self.chunks.append(
            Chunk(
                chunk_id=chunk_id(self.meta.doc_id, self.meta.version, index),
                doc_id=self.meta.doc_id,
                doc_name=self.meta.doc_name,
                version=self.meta.version,
                index=index,
                text=text,
                page=page,
                page_end=page,
                section=self.section_path,
                language=detect_language(text),
                acl_groups=self.meta.acl_groups,
            )
        )

    def flush(self, *, keep_overlap: bool) -> None:
        if self.buf:
            text = " ".join(self.buf)
            self.emit(text, self.page)
            self.carry = _tail(text, self.overlap) if keep_overlap else ""
        else:
            self.carry = ""
        self.buf = []

    def add_prose(self, text: str, page: int | None) -> None:
        if page != self.page:
            self.flush(keep_overlap=False)
            self.page = page
        for piece in _split_long(text, self.max_chars):
            current = sum(len(b) + 1 for b in self.buf)
            if self.buf and current + len(piece) > self.max_chars:
                self.flush(keep_overlap=True)
            if not self.buf and self.carry and len(self.carry) + len(piece) < self.max_chars:
                self.buf.append(self.carry)
            self.carry = ""
            self.buf.append(piece)

    def add_heading(self, block: Block) -> None:
        self.flush(keep_overlap=False)
        level = block.level or 1
        self.section = [(lvl, t) for lvl, t in self.section if lvl < level]
        self.section.append((level, block.text.strip()))
        if block.page is not None:
            self.page = block.page

    def add_table(self, block: Block) -> None:
        self.flush(keep_overlap=False)
        if block.page is not None:
            self.page = block.page
        for part in _split_table(block.text, self.max_chars):
            self.emit(part, self.page)


def chunk_blocks(
    blocks: Sequence[Block], meta: DocMeta, *, max_chars: int, overlap_chars: int
) -> list[Chunk]:
    if overlap_chars >= max_chars:
        raise ValueError("overlap must be smaller than max_chars")
    builder = _Builder(meta, max_chars, overlap_chars)
    for block in blocks:
        if block.kind is BlockKind.HEADING:
            builder.add_heading(block)
        elif block.kind is BlockKind.TABLE:
            builder.add_table(block)
        else:
            builder.add_prose(block.text, block.page)
    builder.flush(keep_overlap=False)
    return builder.chunks
