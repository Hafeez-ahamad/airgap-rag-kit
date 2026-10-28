import pytest

from airgap.core.types import Chunk, ScoredChunk
from airgap.retrieval import bm25
from airgap.retrieval.citations import verify
from airgap.retrieval.fusion import reciprocal_rank_fusion
from airgap.retrieval.gate import ConfidenceGate


def _chunk(cid: str, text: str = "x") -> Chunk:
    return Chunk(cid, "d", "doc.txt", 1, 0, text, 4, 4, "Sec", "en", ("default",))


def _scored(cid: str, score: float = 0.0) -> ScoredChunk:
    return ScoredChunk(_chunk(cid), score)


def test_bm25_tokenizes_indic_words_whole() -> None:
    assert bm25.tokenize("पानी की टंकी") == ["पानी", "की", "टंकी"]
    assert bm25.tokenize("The Pump is OFF") == ["pump", "off"]


def test_bm25_document_weights_saturate() -> None:
    once = bm25.encode_document("valve")
    many = bm25.encode_document(" ".join(["valve"] * 20))
    assert once.indices == many.indices
    assert many.values[0] > once.values[0]
    assert many.values[0] < (bm25.K1 + 1) * 1.01


def test_rrf_rewards_agreement() -> None:
    dense = [_scored("a"), _scored("b"), _scored("c")]
    sparse = [_scored("c"), _scored("a"), _scored("d")]
    fused = reciprocal_rank_fusion([dense, sparse])
    assert [s.chunk.chunk_id for s in fused][:2] == ["a", "c"]
    assert {s.chunk.chunk_id for s in fused} == {"a", "b", "c", "d"}


def test_gate_passes_and_refuses_on_threshold() -> None:
    gate = ConfidenceGate(0.5)
    assert gate.evaluate([_scored("a", 0.51)]).passed
    assert not gate.evaluate([_scored("a", 0.49)]).passed
    assert not gate.evaluate([]).passed
    strict = ConfidenceGate(0.5, tier_offset=0.2)
    assert not strict.evaluate([_scored("a", 0.6)]).passed
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        ConfidenceGate(0.9, tier_offset=0.2)


CONTEXT = [
    _chunk("c1", "The pump must be serviced every 500 operating hours."),
    _chunk("c2", "Warranty covers parts for two years."),
]


def test_valid_citation_passes() -> None:
    v = verify('Service it every 500 hours [1: "serviced every 500 operating hours"].', CONTEXT)
    assert v.grounded
    assert v.display_text == "Service it every 500 hours [1]."
    assert v.citations[0].chunk is CONTEXT[0]


def test_invented_quote_is_caught() -> None:
    v = verify('Service it weekly [1: "serviced every single week"].', CONTEXT)
    assert not v.grounded
    assert v.citations[0].reason == "quote not found in source"


def test_wrong_source_number_and_short_quote_fail() -> None:
    assert verify('[2: "serviced every 500 operating hours"]', CONTEXT).citations[0].valid is False
    assert verify('[3: "Warranty covers parts"]', CONTEXT).citations[0].reason == "no such source"
    assert verify('[2: "parts"]', CONTEXT).citations[0].reason == "quote too short to verify"


def test_normalisation_tolerates_case_whitespace_and_curly_quotes() -> None:
    v = verify("Two years [2: “warranty   COVERS parts\nfor two years”].", CONTEXT)
    assert v.grounded


def test_bare_marker_or_no_citation_is_not_grounded() -> None:
    assert not verify("Warranty is two years [2].", CONTEXT).grounded
    assert not verify("Warranty is two years.", CONTEXT).grounded


def test_segments_label_uncited_sentences() -> None:
    v = verify(
        'Parts are covered [2: "covers parts for two years"]. Pumps are usually reliable.',
        CONTEXT,
    )
    assert [(s.grounded) for s in v.segments] == [True, False]
