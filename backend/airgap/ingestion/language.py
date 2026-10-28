"""Cheap script-based language tag for chunk metadata (not a full language identifier)."""

from __future__ import annotations

# Unicode block -> tag. Devanagari is tagged "hi" although it also covers Marathi/Nepali;
# the tag is used for filtering and reporting, not for model behaviour.
_SCRIPTS: list[tuple[int, int, str]] = [
    (0x0900, 0x097F, "hi"),
    (0x0980, 0x09FF, "bn"),
    (0x0A00, 0x0A7F, "pa"),
    (0x0A80, 0x0AFF, "gu"),
    (0x0B00, 0x0B7F, "or"),
    (0x0B80, 0x0BFF, "ta"),
    (0x0C00, 0x0C7F, "te"),
    (0x0C80, 0x0CFF, "kn"),
    (0x0D00, 0x0D7F, "ml"),
]


def detect_language(text: str) -> str:
    counts: dict[str, int] = {}
    latin = 0
    for ch in text:
        code = ord(ch)
        if ch.isascii():
            latin += ch.isalpha()
            continue
        for lo, hi, tag in _SCRIPTS:
            if lo <= code <= hi:
                counts[tag] = counts.get(tag, 0) + 1
                break
    if counts:
        tag, n = max(counts.items(), key=lambda kv: kv[1])
        if n >= latin * 0.3:
            return tag
    return "en" if latin else "und"
