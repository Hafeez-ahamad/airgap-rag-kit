"""OCR adapter: binary discovery and language-pack handling (no Tesseract needed).

Mock-adapter level: the tesseract process is replaced by a fake runner. The real-binary test
lives in test_ingestion.py and is skipped when Tesseract is not installed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from airgap.capacity.hardware import GIB, HardwareReport, ReportSource, RuntimeInfo
from airgap.capacity.probe import CommandResult, ProbeEnv, RealHardwareProvider
from airgap.ingestion.ocr import TesseractOCR, find_tesseract, parse_langs

LIST = (
    'List of available languages in "C:\\Program Files\\Tesseract-OCR/tessdata/" (3):\n'
    "eng\nosd\nhin\n"
)


def _runner(langs_output: str, calls: list[list[str]]):  # type: ignore[no-untyped-def]
    def run(args: list[str], stdin: bytes | None) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        if "--list-langs" in args:
            return subprocess.CompletedProcess(args, 0, langs_output.encode(), b"")
        return subprocess.CompletedProcess(args, 0, b"recognised text", b"")

    return run


def test_find_tesseract_order(tmp_path: Path) -> None:
    exe = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    # explicit setting wins, but only if it exists
    assert find_tesseract(str(exe), which=lambda n: None) == str(exe)
    assert find_tesseract(str(tmp_path / "nope.exe"), which=lambda n: "/usr/bin/x") is None
    # PATH next
    assert (
        find_tesseract(which=lambda n: "/usr/bin/tesseract", candidates=[exe])
        == "/usr/bin/tesseract"
    )
    # then standard install locations (the Windows installer does not touch PATH)
    assert find_tesseract(which=lambda n: None, candidates=[tmp_path / "x", exe]) == str(exe)
    assert find_tesseract(which=lambda n: None, candidates=[tmp_path / "x"]) is None


def test_parse_langs_ignores_header_and_osd() -> None:
    assert parse_langs(LIST) == {"eng", "hin"}
    assert parse_langs("") == set()


def test_only_installed_languages_are_requested() -> None:
    calls: list[list[str]] = []
    ocr = TesseractOCR("tesseract", runner=_runner(LIST.replace("hin\n", ""), calls), locate=False)
    assert ocr.image_to_text(b"png", ["eng", "hin"]) == "recognised text"
    ocr_call = calls[-1]
    assert ocr_call[ocr_call.index("-l") + 1] == "eng"
    assert ocr.missing_languages == {"hin"}


def test_falls_back_to_english_when_no_requested_pack_exists() -> None:
    calls: list[list[str]] = []
    ocr = TesseractOCR(
        "tesseract",
        runner=_runner("List of available languages (2):\neng\nosd\n", calls),
        locate=False,
    )
    ocr.image_to_text(b"png", ["hin"])
    assert calls[-1][calls[-1].index("-l") + 1] == "eng"


def test_no_language_data_is_a_clear_error() -> None:
    ocr = TesseractOCR(
        "tesseract", runner=_runner("List of available languages (0):\n", []), locate=False
    )
    with pytest.raises(RuntimeError, match="no language data"):
        ocr.image_to_text(b"png", ["eng"])


def test_unavailable_binary() -> None:
    ocr = TesseractOCR(None, locate=False)
    assert not ocr.available()
    with pytest.raises(RuntimeError, match="not installed"):
        ocr.image_to_text(b"png", ["eng"])


def test_probe_finds_tesseract_outside_path_and_lists_languages(tmp_path: Path) -> None:
    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"")

    def run(args: list[str], timeout: float) -> CommandResult | None:
        if args[0] == "tesseract":
            return None  # not on PATH
        if args[0] == str(exe):
            if "--list-langs" in args:
                return CommandResult(0, LIST)
            return CommandResult(0, "tesseract v5.4.0.20240606")
        return None

    env = ProbeEnv(
        system="Windows",
        machine="AMD64",
        run=run,
        read_file=lambda p: None,
        http=lambda u, t: None,
        disk_free=lambda p: 10 * GIB,
        windows_memory=lambda: (8 * GIB, 4 * GIB),
        windows_cpu=lambda: (None, None),
        tesseract_paths=[str(exe)],
    )
    t = RealHardwareProvider(tmp_path, env=env).report().runtime("tesseract")
    assert t is not None and t.installed and t.version == "5.4.0"
    assert t.detail == "languages: eng, hin"


def test_prerequisite_reports_missing_language_packs() -> None:
    from airgap.capacity.catalog import load_catalog
    from airgap.capacity.planner import assess
    from airgap.setup.prerequisites import CheckStatus, check_prerequisites

    report = HardwareReport(
        source=ReportSource.REAL,
        os="linux",
        arch="amd64",
        ram_total_bytes=32 * GIB,
        ram_available_bytes=28 * GIB,
        disk_free_bytes=500 * GIB,
        gpu_probe_ok=True,
        runtimes=[
            RuntimeInfo(name="tesseract", installed=True, version="5.4.0", detail="languages: eng")
        ],
    )
    catalog = load_catalog()
    checks = {
        c.id: c
        for c in check_prerequisites(
            report, assess(report, catalog), catalog, ocr_languages=("eng", "hin")
        )
    }
    ocr = checks["ocr"]
    assert ocr.status is CheckStatus.OUTDATED and "missing language packs: hin" in (
        ocr.detail or ""
    )
    assert not ocr.required  # OCR never blocks setup
