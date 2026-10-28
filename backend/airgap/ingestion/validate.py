"""Upload checks: content-sniffed type allow-list, size limits, filename sanitisation.

The file type is decided from the bytes, and the extension must agree with it.
Nothing here executes or renders file content.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from enum import StrEnum


class FileType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    TXT = "txt"
    MD = "md"
    CSV = "csv"


_EXTENSIONS: dict[str, FileType] = {
    ".pdf": FileType.PDF,
    ".docx": FileType.DOCX,
    ".xlsx": FileType.XLSX,
    ".txt": FileType.TXT,
    ".md": FileType.MD,
    ".markdown": FileType.MD,
    ".csv": FileType.CSV,
}
_TEXT_TYPES = {FileType.TXT, FileType.MD, FileType.CSV}

_WINDOWS_RESERVED = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_MAX_NAME = 200
_MAX_DEPTH = 16


class UploadRejected(ValueError):
    """Raised with a message that is safe to show to the user."""


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    name: str  # sanitised relative path, "/"-separated
    file_type: FileType
    data: bytes


def sanitize_component(name: str) -> str:
    name = unicodedata.normalize("NFC", name)
    name = _UNSAFE_CHARS.sub("_", name).strip(" .")
    if not name or name in {".", ".."}:
        raise UploadRejected("invalid file or folder name")
    stem = name.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        name = f"_{name}"
    if len(name) > _MAX_NAME:
        root, dot, ext = name.rpartition(".")
        name = (
            (root[: _MAX_NAME - len(ext) - 1] + dot + ext)
            if dot and len(ext) <= 10
            else name[:_MAX_NAME]
        )
    return name


def sanitize_relative_path(path: str) -> str:
    """Turn a client-supplied (possibly folder-relative) path into a safe logical name."""
    parts = [p for p in re.split(r"[\\/]+", path) if p not in {"", "."}]
    if not parts:
        raise UploadRejected("empty file name")
    if any(p == ".." for p in parts):
        raise UploadRejected("path traversal is not allowed in file names")
    if len(parts) > _MAX_DEPTH:
        raise UploadRejected("folder nesting is too deep")
    return "/".join(sanitize_component(p) for p in parts)


def _zip_kind(data: bytes, max_uncompressed: int) -> FileType | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            total = sum(i.file_size for i in infos)
            if total > max_uncompressed:
                raise UploadRejected("archive expands beyond the allowed size")
            names = {i.filename for i in infos}
    except zipfile.BadZipFile:
        return None
    if "[Content_Types].xml" not in names:
        return None
    if "word/document.xml" in names:
        return FileType.DOCX
    if "xl/workbook.xml" in names:
        return FileType.XLSX
    return None


def _is_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return True


def sniff(data: bytes, max_uncompressed: int) -> FileType | None:
    """Content-based type detection. Returns None for anything not on the allow-list."""
    if data.startswith(b"%PDF-"):
        return FileType.PDF
    if data.startswith(b"PK\x03\x04"):
        return _zip_kind(data, max_uncompressed)
    if _is_text(data):
        return FileType.TXT  # refined by extension below (txt / md / csv)
    return None


def validate_upload(
    filename: str, data: bytes, *, max_bytes: int, max_uncompressed: int
) -> ValidatedUpload:
    name = sanitize_relative_path(filename)
    if len(data) == 0:
        raise UploadRejected(f"{name}: file is empty")
    if len(data) > max_bytes:
        raise UploadRejected(
            f"{name}: file is larger than the {max_bytes // (1024 * 1024)} MB limit"
        )
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name.rsplit("/", 1)[-1] else ""
    claimed = _EXTENSIONS.get(ext)
    if claimed is None:
        allowed = ", ".join(sorted(_EXTENSIONS))
        raise UploadRejected(f"{name}: file type not allowed (allowed: {allowed})")
    sniffed = sniff(data, max_uncompressed)
    if sniffed is None:
        raise UploadRejected(f"{name}: content is not a supported document type")
    if claimed in _TEXT_TYPES and sniffed is FileType.TXT:
        return ValidatedUpload(name, claimed, data)
    if claimed is not sniffed:
        raise UploadRejected(f"{name}: content does not match its {ext} extension")
    return ValidatedUpload(name, sniffed, data)
