"""Plain text and Markdown."""

from __future__ import annotations

import re

from airgap.core.types import Block, BlockKind, ParsedDocument
from airgap.ingestion.headings import heading_level

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def decode(data: bytes) -> str:
    return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def _paragraphs(lines: list[str]) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for line in lines:
        if line.strip():
            buf.append(line.strip())
        elif buf:
            out.append(" ".join(buf))
            buf = []
    if buf:
        out.append(" ".join(buf))
    return out


def parse_text(data: bytes) -> ParsedDocument:
    blocks: list[Block] = []
    for para in _paragraphs(decode(data).split("\n")):
        level = heading_level(para)
        if level:
            blocks.append(Block(BlockKind.HEADING, para, level=level))
        else:
            blocks.append(Block(BlockKind.PARAGRAPH, para))
    return ParsedDocument(blocks)


def parse_markdown(data: bytes) -> ParsedDocument:
    blocks: list[Block] = []
    para: list[str] = []
    table: list[str] = []
    in_code = False

    def flush_para() -> None:
        if para:
            blocks.append(Block(BlockKind.PARAGRAPH, " ".join(para)))
            para.clear()

    def flush_table() -> None:
        if table:
            blocks.append(Block(BlockKind.TABLE, "\n".join(table)))
            table.clear()

    code: list[str] = []
    for raw in decode(data).split("\n"):
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            flush_para()
            flush_table()
            code.append(line)
            if in_code:
                blocks.append(Block(BlockKind.PARAGRAPH, "\n".join(code)))
                code.clear()
            in_code = not in_code
            continue
        if in_code:
            code.append(line)
            continue
        heading = _MD_HEADING.match(line)
        if heading:
            flush_para()
            flush_table()
            blocks.append(Block(BlockKind.HEADING, heading.group(2), level=len(heading.group(1))))
        elif _MD_TABLE_ROW.match(line):
            flush_para()
            table.append(line.strip())
        elif not line.strip():
            flush_para()
            flush_table()
        else:
            flush_table()
            para.append(line.strip())
    if code:  # unterminated fence
        blocks.append(Block(BlockKind.PARAGRAPH, "\n".join(code)))
    flush_para()
    flush_table()
    return ParsedDocument(blocks)
