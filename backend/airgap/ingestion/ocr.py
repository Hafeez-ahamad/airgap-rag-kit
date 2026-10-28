"""Tesseract OCR via its command-line binary (no network, no shell)."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence

_TIMEOUT_S = 120


class TesseractOCR:
    def __init__(self, binary: str = "tesseract") -> None:
        self._binary = shutil.which(binary)

    def available(self) -> bool:
        return self._binary is not None

    def image_to_text(self, png: bytes, languages: Sequence[str]) -> str:
        if self._binary is None:
            raise RuntimeError("tesseract is not installed")
        langs = "+".join(languages) or "eng"
        result = subprocess.run(  # noqa: S603 - fixed binary path, no shell, args are ours
            [self._binary, "stdin", "stdout", "-l", langs, "--psm", "3"],
            input=png,
            capture_output=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"tesseract failed: {result.stderr.decode(errors='replace')[:200]}")
        return result.stdout.decode("utf-8", errors="replace")
