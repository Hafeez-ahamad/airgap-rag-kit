import io
import zipfile

import pytest

from airgap.ingestion.validate import (
    FileType,
    UploadRejected,
    sanitize_relative_path,
    validate_upload,
)
from tests.fixture_files import docx_file, text_pdf, xlsx_file

LIMITS = {"max_bytes": 1000_000, "max_uncompressed": 5_000_000}


def test_accepts_supported_types_by_content() -> None:
    assert validate_upload("a.pdf", text_pdf([["hi"]]), **LIMITS).file_type is FileType.PDF
    assert validate_upload("a.docx", docx_file("H", ["p"]), **LIMITS).file_type is FileType.DOCX
    assert validate_upload("a.xlsx", xlsx_file({"S": [["a"]]}), **LIMITS).file_type is FileType.XLSX
    assert validate_upload("a.csv", b"a,b\n1,2\n", **LIMITS).file_type is FileType.CSV
    assert validate_upload("a.md", b"# t\n", **LIMITS).file_type is FileType.MD
    assert validate_upload("a.txt", "नमस्ते".encode(), **LIMITS).file_type is FileType.TXT


def test_rejects_disallowed_extension() -> None:
    with pytest.raises(UploadRejected, match="file type not allowed"):
        validate_upload("run.exe", b"MZ\x90\x00", **LIMITS)


def test_rejects_content_that_does_not_match_extension() -> None:
    with pytest.raises(UploadRejected, match="does not match"):
        validate_upload("fake.pdf", docx_file("H", ["p"]), **LIMITS)
    with pytest.raises(UploadRejected, match="not a supported"):
        validate_upload("bin.txt", b"\x00\x01\x02binary", **LIMITS)
    with pytest.raises(UploadRejected, match="does not match"):
        validate_upload("doc.docx", text_pdf([["x"]]), **LIMITS)


def test_rejects_zip_that_is_not_office() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("payload.sh", "echo hi")
    with pytest.raises(UploadRejected, match="not a supported"):
        validate_upload("x.docx", buf.getvalue(), **LIMITS)


def test_rejects_zip_bomb_by_declared_size() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<x/>")
        zf.writestr("word/document.xml", "0" * 6_000_000)
    with pytest.raises(UploadRejected, match="expands beyond"):
        validate_upload("bomb.docx", buf.getvalue(), **LIMITS)


def test_rejects_oversized_and_empty_files() -> None:
    with pytest.raises(UploadRejected, match="larger than"):
        validate_upload("big.txt", b"a" * 1_000_001, **LIMITS)
    with pytest.raises(UploadRejected, match="empty"):
        validate_upload("e.txt", b"", **LIMITS)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("folder\\sub/report.pdf", "folder/sub/report.pdf"),
        ("./a//b.txt", "a/b.txt"),
        ('bad<>:"|?*name.txt', "bad_______name.txt"),
        ("CON.txt", "_CON.txt"),
        ("tab\tname.md", "tab_name.md"),
    ],
)
def test_sanitizes_names(raw: str, expected: str) -> None:
    assert sanitize_relative_path(raw) == expected


@pytest.mark.parametrize("raw", ["../etc/passwd.txt", "a/../../b.txt", "", "/", " . "])
def test_rejects_traversal_and_empty_names(raw: str) -> None:
    with pytest.raises(UploadRejected):
        sanitize_relative_path(raw)
