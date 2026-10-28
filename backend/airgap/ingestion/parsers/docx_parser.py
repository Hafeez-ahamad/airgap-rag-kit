"""DOCX via python-docx: heading styles become headings, tables are kept whole."""

from __future__ import annotations

import io
import re

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from airgap.core.types import Block, BlockKind, ParsedDocument

_HEADING_STYLE = re.compile(r"^(Heading|Title)\s*(\d*)$", re.IGNORECASE)


def _table_text(table: Table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells = [c.text.strip().replace("\n", " ") for c in row.cells]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def parse_docx(data: bytes) -> ParsedDocument:
    document = Document(io.BytesIO(data))
    blocks: list[Block] = []
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = item.text.strip()
            if not text:
                continue
            style = item.style.name if item.style is not None else ""
            match = _HEADING_STYLE.match(style or "")
            if match:
                level = int(match.group(2)) if match.group(2) else 1
                blocks.append(Block(BlockKind.HEADING, text, level=min(level, 6)))
            else:
                blocks.append(Block(BlockKind.PARAGRAPH, text))
        elif isinstance(item, Table):
            text = _table_text(item)
            if text.strip("| \n"):
                blocks.append(Block(BlockKind.TABLE, text))
    return ParsedDocument(blocks)
