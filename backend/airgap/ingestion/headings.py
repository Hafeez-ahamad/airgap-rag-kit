"""Heading heuristics for sources without style information (PDF text, plain text)."""

from __future__ import annotations

import re

_NUMBERED = re.compile(r"^(\d+(\.\d+){0,4})\.?\s+\S")
_MAX_HEADING_CHARS = 90


def heading_level(line: str) -> int:
    """Return a heading level (1-5) if the line looks like a heading, else 0."""
    text = line.strip()
    if not text or len(text) > _MAX_HEADING_CHARS:
        return 0
    if text.endswith((".", ",", ";", ":")) and not _NUMBERED.match(text):
        return 0
    numbered = _NUMBERED.match(text)
    if numbered:
        if text.endswith((".", ",", ";")):
            return 0
        words = text.split()
        if len(words) > 12:
            return 0
        return min(numbered.group(1).count(".") + 1, 5)
    letters = [c for c in text if c.isalpha()]
    if (
        len(letters) >= 3
        and all(c.isupper() for c in letters if c.isascii())
        and any(c.isascii() for c in letters)
    ):
        return 1
    return 0
