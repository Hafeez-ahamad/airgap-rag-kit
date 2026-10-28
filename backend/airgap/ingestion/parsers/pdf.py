"""PDF text extraction with per-page OCR fallback for image-only pages.

Uses pypdfium2 (PDFium; BSD-3/Apache-2.0). See docs/decisions/0003-pdf-library.md.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

import pypdfium2 as pdfium

from airgap.core.ports import OCREngine
from airgap.core.types import Block, BlockKind, ParsedDocument
from airgap.ingestion.headings import heading_level

_OCR_DPI = 300
_MAX_PAGES = 5000


def _page_blocks(text: str, page_no: int) -> list[Block]:
    blocks: list[Block] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            blocks.append(Block(BlockKind.PARAGRAPH, " ".join(buf), page=page_no))
            buf.clear()

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        level = heading_level(line)
        if level:
            flush()
            blocks.append(Block(BlockKind.HEADING, line, page=page_no, level=level))
            continue
        buf.append(line)
        if line.endswith((".", "?", "!", ":")) and len(" ".join(buf)) > 400:
            flush()
    flush()
    return blocks


def _render_png(page: pdfium.PdfPage) -> bytes:
    image = page.render(scale=_OCR_DPI / 72).to_pil()
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def parse_pdf(
    data: bytes, *, ocr: OCREngine, languages: Sequence[str], min_text_chars: int
) -> ParsedDocument:
    pdf = pdfium.PdfDocument(data)
    try:
        if len(pdf) > _MAX_PAGES:
            raise ValueError("too many pages")
        blocks: list[Block] = []
        ocr_pages: list[int] = []
        for index in range(len(pdf)):
            page = pdf[index]
            page_no = index + 1
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                finally:
                    textpage.close()
                if len(text.strip()) < min_text_chars and ocr.available():
                    text = ocr.image_to_text(_render_png(page), languages)
                    ocr_pages.append(page_no)
                blocks.extend(_page_blocks(text, page_no))
            finally:
                page.close()
        return ParsedDocument(blocks, ocr_pages)
    finally:
        pdf.close()
