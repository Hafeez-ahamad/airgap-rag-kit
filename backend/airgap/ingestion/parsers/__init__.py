"""Format-specific parsers. Each turns bytes into structural blocks."""

from __future__ import annotations

from collections.abc import Sequence

from airgap.core.ports import OCREngine
from airgap.core.types import ParsedDocument
from airgap.ingestion.parsers.docx_parser import parse_docx
from airgap.ingestion.parsers.pdf import parse_pdf
from airgap.ingestion.parsers.tabular import parse_csv, parse_xlsx
from airgap.ingestion.parsers.text import parse_markdown, parse_text
from airgap.ingestion.validate import FileType


class ParseError(ValueError):
    """The file passed validation but could not be parsed."""


def parse(
    file_type: FileType,
    data: bytes,
    *,
    ocr: OCREngine,
    ocr_languages: Sequence[str],
    ocr_min_text_chars: int,
) -> ParsedDocument:
    try:
        match file_type:
            case FileType.PDF:
                return parse_pdf(
                    data, ocr=ocr, languages=ocr_languages, min_text_chars=ocr_min_text_chars
                )
            case FileType.DOCX:
                return parse_docx(data)
            case FileType.XLSX:
                return parse_xlsx(data)
            case FileType.CSV:
                return parse_csv(data)
            case FileType.MD:
                return parse_markdown(data)
            case FileType.TXT:
                return parse_text(data)
    except ParseError:
        raise
    except Exception as exc:  # parser libraries raise many types; report one
        raise ParseError(f"could not parse {file_type.value} file: {type(exc).__name__}") from exc
