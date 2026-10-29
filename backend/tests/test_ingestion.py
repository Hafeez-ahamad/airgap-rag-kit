import pytest

from airgap.core.ports import SparseVector
from airgap.core.types import DocStatus
from airgap.ingestion.documents import Outcome
from airgap.ingestion.ocr import find_tesseract
from airgap.ingestion.parsers import parse
from airgap.ingestion.validate import FileType
from airgap.retrieval import bm25
from airgap.testing.fakes import FakeOCR
from tests.conftest import Kit
from tests.fixture_files import docx_file, scanned_pdf, text_pdf, xlsx_file


def _status(kit: Kit, doc_id: str) -> tuple[str, str | None]:
    doc = kit.documents.get(doc_id)
    assert doc is not None
    return doc.status, doc.error


def _sparse_hits(kit: Kit, text: str) -> list[str]:
    return [s.chunk.text for s in kit.store.search_sparse(bm25.encode_query(text), ["default"], 20)]


def _dense_hits(kit: Kit, text: str) -> list[str]:
    vec = kit.embedder.embed([text])[0]
    return [s.chunk.text for s in kit.store.search_dense(vec, ["default"], 20)]


def test_every_supported_format_reaches_ready(kit: Kit) -> None:
    kit.ocr = FakeOCR("Scanned invoice total is 4200 rupees.")
    kit.pipeline._ocr = kit.ocr  # share the fake with the pipeline
    files = [
        ("manual.pdf", text_pdf([["1 Overview", "The boiler runs at 80 degrees."], ["Page two."]])),
        ("scan.pdf", scanned_pdf(["Scanned invoice total is 4200 rupees."])),
        ("policy.docx", docx_file("Leave policy", ["Staff get 24 days of leave."])),
        ("parts.csv", b"part,qty\nvalve,4\npump,2\n"),
        ("stock.xlsx", xlsx_file({"Store": [["item", "count"], ["gasket", 12]]})),
        ("notes/readme.md", b"# Notes\n\nCalibrate sensors yearly.\n"),
        ("notes/sub/plain.txt", b"Plain text file about filters."),
    ]
    results = kit.documents.upload(files, "u1")
    assert [r.outcome for r in results] == [Outcome.QUEUED] * len(files)
    assert results[5].filename == "notes/readme.md"  # folder path kept
    kit.drain()
    for r in results:
        assert r.doc_id is not None
        assert _status(kit, r.doc_id) == (DocStatus.READY.value, None), r.filename
    scan = kit.documents.get(results[1].doc_id or "")
    assert scan is not None and scan.ocr_pages == [1]
    assert any("4200 rupees" in t for t in _sparse_hits(kit, "invoice total"))


def test_rejections_do_not_block_other_files(kit: Kit) -> None:
    results = kit.documents.upload(
        [("big.txt", b"a" * 1_000_001), ("tool.exe", b"MZ"), ("ok.txt", b"Fine content here.")],
        "u1",
    )
    assert [r.outcome for r in results] == [Outcome.REJECTED, Outcome.REJECTED, Outcome.QUEUED]
    assert "larger than" in (results[0].message or "")
    assert "not allowed" in (results[1].message or "")


def test_unchanged_reupload_is_a_noop_and_duplicate_content_is_detected(kit: Kit) -> None:
    first = kit.documents.upload([("a.txt", b"Gearbox oil is ISO VG 220.")], "u1")[0]
    kit.drain()
    again = kit.documents.upload([("a.txt", b"Gearbox oil is ISO VG 220.")], "u1")[0]
    assert again.outcome is Outcome.UNCHANGED
    assert kit.queue.items == []
    twin = kit.documents.upload([("copy.txt", b"Gearbox oil is ISO VG 220.")], "u1")[0]
    assert twin.outcome is Outcome.DUPLICATE and twin.doc_id == first.doc_id
    assert len(kit.documents.list()) == 1


def test_changed_file_replaces_old_version_in_dense_and_sparse(kit: Kit) -> None:
    v1 = kit.documents.upload([("spec.txt", b"Torque setting is 45 newton metres.")], "u1")[0]
    kit.drain()
    assert _sparse_hits(kit, "45 newton") == ["Torque setting is 45 newton metres."]
    v2 = kit.documents.upload([("spec.txt", b"Torque setting is 60 newton metres.")], "u1")[0]
    assert (v2.outcome, v2.doc_id, v2.version) == (Outcome.NEW_VERSION, v1.doc_id, 2)
    kit.drain()
    assert _sparse_hits(kit, "torque setting") == ["Torque setting is 60 newton metres."]
    assert "Torque setting is 45 newton metres." not in _dense_hits(kit, "torque setting")
    assert kit.store.count_document_chunks(v1.doc_id or "") == 1
    doc = kit.documents.get(v1.doc_id or "")
    assert doc is not None and doc.current_version == 2


def test_delete_removes_file_versions_and_all_index_entries(kit: Kit) -> None:
    r = kit.documents.upload([("gone.txt", b"Temporary note about coolant.")], "u1")[0]
    kit.drain()
    kit.documents.upload([("gone.txt", b"Updated note about coolant levels.")], "u1")
    kit.drain()
    assert kit.documents.delete(r.doc_id or "")
    assert kit.store.count_document_chunks(r.doc_id or "") == 0
    assert _sparse_hits(kit, "coolant") == []
    assert kit.blobs.data == {}
    assert kit.documents.get(r.doc_id or "") is None
    assert not kit.documents.delete(r.doc_id or "")


def test_unparseable_and_empty_documents_fail_with_reason(kit: Kit) -> None:
    kit.pipeline._ocr = FakeOCR(available=False)
    broken = kit.documents.upload([("broken.pdf", b"%PDF-1.4 garbage")], "u1")[0]
    blank = kit.documents.upload([("blank.pdf", scanned_pdf([]))], "u1")[0]
    kit.drain()
    status, error = _status(kit, broken.doc_id or "")
    assert status == DocStatus.FAILED.value and "could not parse pdf" in (error or "")
    status, error = _status(kit, blank.doc_id or "")
    assert status == DocStatus.FAILED.value and "OCR is not available" in (error or "")


def test_superseded_job_is_skipped(kit: Kit) -> None:
    kit.documents.upload([("s.txt", b"Version one text.")], "u1")
    kit.documents.upload([("s.txt", b"Version two text.")], "u1")
    kit.drain()  # job for v1 is skipped; only v2 is indexed
    assert _sparse_hits(kit, "version text") == ["Version two text."]


def test_empty_sparse_query_returns_nothing(kit: Kit) -> None:
    assert kit.store.search_sparse(SparseVector([], []), ["default"], 5) == []


def test_text_pdf_keeps_pages_and_numbered_headings() -> None:
    data = text_pdf([["2.1 Startup", "Open valve A."], ["3 Shutdown", "Close valve A."]])
    parsed = parse(FileType.PDF, data, ocr=FakeOCR(), ocr_languages=["eng"], ocr_min_text_chars=5)
    headings = [(b.text, b.page, b.level) for b in parsed.blocks if b.kind == "heading"]
    assert headings == [("2.1 Startup", 1, 2), ("3 Shutdown", 2, 1)]
    assert parsed.ocr_pages == []


@pytest.mark.skipif(find_tesseract() is None, reason="tesseract binary not installed")
def test_real_tesseract_reads_scanned_pdf() -> None:
    from airgap.ingestion.ocr import TesseractOCR

    parsed = parse(
        FileType.PDF,
        scanned_pdf(["Emergency stop is the red button."]),
        ocr=TesseractOCR(),
        ocr_languages=["eng"],
        ocr_min_text_chars=5,
    )
    text = " ".join(b.text for b in parsed.blocks).lower()
    assert "emergency stop" in text
