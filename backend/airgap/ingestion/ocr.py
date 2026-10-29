"""Tesseract OCR via its command-line binary (no network, no shell).

Finds the binary where installers put it (PATH is not enough: the UB Mannheim Windows installer
does not add itself to PATH) and only asks for language packs that are installed, so a missing
pack degrades OCR for that language instead of failing every scanned page.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

log = logging.getLogger(__name__)
_TIMEOUT_S = 120


def default_locations() -> list[Path]:
    env = os.environ
    paths = [
        Path(env.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
        Path(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Tesseract-OCR"
        / "tesseract.exe",
        Path(env.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
        Path("/opt/homebrew/bin/tesseract"),
        Path("/usr/local/bin/tesseract"),
        Path("/usr/bin/tesseract"),
    ]
    return [p for p in paths if str(p) not in {"", "."}]


def find_tesseract(
    explicit: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    candidates: Sequence[Path] | None = None,
) -> str | None:
    """Explicit setting (AIRGAP_OCR_BINARY) > PATH > standard install locations."""
    if explicit:
        return explicit if Path(explicit).is_file() else None
    found = which("tesseract")
    if found:
        return found
    for path in candidates if candidates is not None else default_locations():
        if path.is_file():
            return str(path)
    return None


Runner = Callable[[list[str], bytes | None], subprocess.CompletedProcess[bytes]]


def _run(args: list[str], stdin: bytes | None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - fixed binary path, no shell, args are ours
        args, input=stdin, capture_output=True, timeout=_TIMEOUT_S, check=False
    )


def parse_langs(output: str) -> set[str]:
    """`tesseract --list-langs`: a header line, then one language code per line."""
    lines = [x.strip() for x in output.splitlines() if x.strip()]
    return {x for x in lines if not x.lower().startswith("list of available")} - {"osd"}


class TesseractOCR:
    def __init__(
        self, binary: str | None = None, *, runner: Runner = _run, locate: bool = True
    ) -> None:
        self._binary = find_tesseract(binary) if locate else binary
        self._run = runner
        self._langs: set[str] | None = None
        self.missing_languages: set[str] = set()

    @property
    def binary(self) -> str | None:
        return self._binary

    def available(self) -> bool:
        return self._binary is not None

    def installed_languages(self) -> set[str]:
        if self._binary is None:
            return set()
        if self._langs is None:
            res = self._run([self._binary, "--list-langs"], None)
            out = (res.stdout or b"") + (res.stderr or b"")  # older builds print to stderr
            self._langs = parse_langs(out.decode("utf-8", errors="replace"))
        return self._langs

    def _languages(self, requested: Sequence[str]) -> str:
        installed = self.installed_languages()
        wanted = list(requested) or ["eng"]
        usable = [lang for lang in wanted if lang in installed]
        missing = set(wanted) - set(usable)
        if missing - self.missing_languages:
            log.warning("OCR language packs not installed", extra={"missing": sorted(missing)})
            self.missing_languages |= missing
        if not usable:
            if not installed:
                raise RuntimeError("tesseract has no language data installed")
            usable = ["eng"] if "eng" in installed else [sorted(installed)[0]]
        return "+".join(usable)

    def image_to_text(self, png: bytes, languages: Sequence[str]) -> str:
        if self._binary is None:
            raise RuntimeError("tesseract is not installed")
        result = self._run(
            [self._binary, "stdin", "stdout", "-l", self._languages(languages), "--psm", "3"], png
        )
        if result.returncode != 0:
            raise RuntimeError(f"tesseract failed: {result.stderr.decode(errors='replace')[:200]}")
        return result.stdout.decode("utf-8", errors="replace")
