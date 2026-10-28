import itertools

from airgap.core.types import Block, BlockKind
from airgap.ingestion.chunker import DocMeta, chunk_blocks, chunk_id

META = DocMeta("doc-1", "manual.pdf", 2, ("default",))


def _chunks(blocks: list[Block], max_chars: int = 200, overlap: int = 40):  # type: ignore[no-untyped-def]
    return chunk_blocks(blocks, META, max_chars=max_chars, overlap_chars=overlap)


def test_headings_set_section_path_and_start_new_chunks() -> None:
    blocks = [
        Block(BlockKind.HEADING, "Safety", level=1),
        Block(BlockKind.PARAGRAPH, "Wear gloves."),
        Block(BlockKind.HEADING, "Electrical", level=2),
        Block(BlockKind.PARAGRAPH, "Isolate power first."),
        Block(BlockKind.HEADING, "Maintenance", level=1),
        Block(BlockKind.PARAGRAPH, "Oil monthly."),
    ]
    chunks = _chunks(blocks)
    assert [c.section for c in chunks] == [
        "Safety",
        "Safety > Electrical",
        "Maintenance",
    ]
    assert [c.text for c in chunks] == ["Wear gloves.", "Isolate power first.", "Oil monthly."]


def test_chunks_never_span_pages() -> None:
    blocks = [
        Block(BlockKind.PARAGRAPH, "Page one text.", page=1),
        Block(BlockKind.PARAGRAPH, "Page two text.", page=2),
    ]
    chunks = _chunks(blocks)
    assert [(c.page, c.text) for c in chunks] == [(1, "Page one text."), (2, "Page two text.")]


def test_long_prose_is_split_with_overlap() -> None:
    sentences = [f"Sentence number {i} talks about pumps and valves." for i in range(12)]
    chunks = _chunks([Block(BlockKind.PARAGRAPH, " ".join(sentences))], max_chars=200, overlap=40)
    assert len(chunks) > 1
    assert all(len(c.text) <= 200 + 40 for c in chunks)
    for prev, nxt in itertools.pairwise(chunks):
        tail_words = prev.text.split()[-3:]
        assert " ".join(tail_words) in nxt.text  # overlap carried forward


def test_tables_are_separate_chunks_and_split_with_header() -> None:
    header = "| part | qty |"
    rows = [f"| part-{i:03d} | {i} |" for i in range(40)]
    blocks = [
        Block(BlockKind.PARAGRAPH, "Intro text."),
        Block(BlockKind.TABLE, "\n".join([header, *rows])),
    ]
    chunks = _chunks(blocks, max_chars=200, overlap=40)
    assert chunks[0].text == "Intro text."
    table_chunks = chunks[1:]
    assert len(table_chunks) > 1
    assert all(c.text.startswith(header) for c in table_chunks)
    joined = "\n".join(c.text for c in table_chunks)
    assert all(r in joined for r in rows)


def test_metadata_and_deterministic_ids() -> None:
    chunks = _chunks([Block(BlockKind.PARAGRAPH, "नमस्ते दुनिया यह परीक्षण है", page=3)])
    chunk = chunks[0]
    assert (chunk.doc_id, chunk.version, chunk.page, chunk.language) == ("doc-1", 2, 3, "hi")
    assert chunk.acl_groups == ("default",)
    assert chunk.chunk_id == chunk_id("doc-1", 2, 0)
    assert chunk_id("doc-1", 2, 0) != chunk_id("doc-1", 3, 0)
