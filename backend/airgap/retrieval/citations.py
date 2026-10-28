"""Citation verification (ADR-0007).

The model must cite as ``[n: "verbatim quote"]`` where ``n`` is the context block number.
A citation is valid only if the normalised quote is an exact substring of block ``n``.
No fuzzy matching: a paraphrase is not a quote.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from airgap.core.types import Chunk

CITATION = re.compile(r"\[(\d{1,2})\s*:\s*[\"“”„«](.+?)[\"“”»]\s*\]", re.DOTALL)
BARE_MARKER = re.compile(r"\[(\d{1,2})\]")
# Split after sentence punctuation, but keep a trailing "[n]" with its sentence.
_SENTENCE = re.compile(r"(?<=[.!?।])\s+(?=[^\s\[])")
# Curly quotes and en/em dashes fold to ASCII (code points, to keep the source ASCII-only).
_QUOTE_MAP = str.maketrans(
    {chr(0x2018): "'", chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"'}
    | {chr(0x2013): "-", chr(0x2014): "-"}
)
MIN_QUOTE_CHARS = 12


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTE_MAP).casefold()
    return " ".join(text.split())


@dataclass(frozen=True, slots=True)
class Citation:
    number: int
    quote: str
    valid: bool
    reason: str
    chunk: Chunk | None


@dataclass(frozen=True, slots=True)
class Segment:
    text: str
    grounded: bool


@dataclass(frozen=True, slots=True)
class Verification:
    citations: list[Citation]
    bare_markers: int  # "[n]" without a quote: unverifiable
    display_text: str
    segments: list[Segment]

    @property
    def all_valid(self) -> bool:
        return all(c.valid for c in self.citations) and self.bare_markers == 0

    @property
    def grounded(self) -> bool:
        """True when there is at least one citation and every citation checks out."""
        return bool(self.citations) and self.all_valid


def _check(number: int, quote: str, context: Sequence[Chunk]) -> Citation:
    if not 1 <= number <= len(context):
        return Citation(number, quote, False, "no such source", None)
    chunk = context[number - 1]
    norm = normalise(quote)
    if len(norm) < MIN_QUOTE_CHARS:
        return Citation(number, quote, False, "quote too short to verify", chunk)
    if norm not in normalise(chunk.text):
        return Citation(number, quote, False, "quote not found in source", chunk)
    return Citation(number, quote, True, "ok", chunk)


def verify(answer: str, context: Sequence[Chunk]) -> Verification:
    citations = [_check(int(m.group(1)), m.group(2), context) for m in CITATION.finditer(answer)]
    without_quotes = CITATION.sub("", answer)
    bare = len(BARE_MARKER.findall(without_quotes))
    display = CITATION.sub(lambda m: f"[{m.group(1)}]", answer)
    valid_numbers = {c.number for c in citations if c.valid}
    segments: list[Segment] = []
    for sentence in _SENTENCE.split(display.strip()):
        if not sentence:
            continue
        cited = {int(n) for n in BARE_MARKER.findall(sentence)}
        segments.append(Segment(sentence, bool(cited) and cited <= valid_numbers))
    return Verification(citations, bare, display, segments)
