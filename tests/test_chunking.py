import pytest

from assistant.ingestion.chunking import chunk_blocks
from assistant.ingestion.models import Block


def test_small_document_is_one_chunk():
    chunks = chunk_blocks([Block("dòng một\ndòng hai", page=2, section="A")], 500, 50)
    assert len(chunks) == 1
    assert chunks[0].text == "dòng một\ndòng hai"
    assert chunks[0].page == 2 and chunks[0].section == "A" and chunks[0].ord == 0


def test_sections_never_mixed():
    blocks = [Block("nội dung A", 1, "A"), Block("nội dung B", 1, "B")]
    chunks = chunk_blocks(blocks, 500, 50)
    assert [c.section for c in chunks] == ["A", "B"]
    assert [c.ord for c in chunks] == [0, 1]


def test_respects_size_and_has_overlap():
    lines = "\n".join(f"Dòng số {i:03d} có nội dung khá dài để chiếm chỗ" for i in range(40))
    chunks = chunk_blocks([Block(lines, 1, "S")], size=300, overlap=100)
    assert len(chunks) > 3
    assert all(len(c.text) <= 300 for c in chunks)
    for a, b in zip(chunks, chunks[1:], strict=False):
        last_line = a.text.split("\n")[-1]
        assert last_line in b.text.split("\n")  # dòng cuối chunk trước lặp lại ở chunk sau
    covered = "\n".join(c.text for c in chunks)
    for i in range(40):
        assert f"Dòng số {i:03d}" in covered  # không mất dòng nào


def test_page_is_page_of_first_line():
    blocks = [Block("a" * 150, 1, "S"), Block("b" * 150, 2, "S"), Block("c" * 150, 3, "S")]
    chunks = chunk_blocks(blocks, size=200, overlap=0)
    assert [c.page for c in chunks] == [1, 2, 3]


def test_long_line_is_split_on_sentences():
    text = ". ".join(f"Câu số {i} nói về một điều gì đó" for i in range(30)) + "."
    chunks = chunk_blocks([Block(text, None, None)], size=200, overlap=0)
    assert all(len(c.text) <= 200 for c in chunks)
    assert len(chunks) > 3


def test_invalid_params():
    with pytest.raises(ValueError):
        chunk_blocks([Block("x")], size=100, overlap=100)


def test_empty_blocks():
    assert chunk_blocks([], 100, 10) == []
    assert chunk_blocks([Block("  \n ")], 100, 10) == []
