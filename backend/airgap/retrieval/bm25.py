"""BM25 as sparse vectors.

Documents carry the BM25 term-frequency component; Qdrant applies IDF at query time
(sparse vector ``modifier=IDF``). ``avg_doc_len`` is a fixed corpus-independent constant
so re-indexing one file never changes other files' vectors (ADR-0006).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

from airgap.core.ports import SparseVector

# \w misses Indic combining marks (category Mn/Mc), so include those blocks explicitly.
_TOKEN = re.compile(r"[\w" + chr(0x0900) + "-" + chr(0x0DFF) + "]+")  # Devanagari..Sinhala
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "what",
        "which",
        "who",
        "how",
        "when",
        "where",
        "why",
        "do",
        "does",
        "did",
    ]
)
K1 = 1.2
B = 0.75
AVG_DOC_LEN = 180.0


def tokenize(text: str) -> list[str]:
    return [t for t in (m.group(0).lower() for m in _TOKEN.finditer(text)) if t not in _STOPWORDS]


def token_index(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big") >> 1


def _to_vector(weights: dict[int, float]) -> SparseVector:
    items = sorted(weights.items())
    return SparseVector([i for i, _ in items], [w for _, w in items])


def encode_document(text: str) -> SparseVector:
    tokens = tokenize(text)
    if not tokens:
        return SparseVector([], [])
    length_norm = 1 - B + B * len(tokens) / AVG_DOC_LEN
    weights: dict[int, float] = {}
    for token, tf in Counter(tokens).items():
        idx = token_index(token)
        weights[idx] = weights.get(idx, 0.0) + tf * (K1 + 1) / (tf + K1 * length_norm)
    return _to_vector(weights)


def encode_query(text: str) -> SparseVector:
    return _to_vector({token_index(t): 1.0 for t in set(tokenize(text))})
