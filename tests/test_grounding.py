from assistant.rag.db import ChunkRecord
from assistant.rag.grounding import (
    Source,
    build_system_prompt,
    clean_rewrite,
    merge_chunks,
    unsupported_numbers,
)


def _chunk(ord_, text, section=None):
    return ChunkRecord(
        id=str(ord_), document_id="d", document="a.txt", title="a", ord=ord_,
        text=text, page=None, section=section, department="x",
    )  # fmt: skip


def test_merge_chunks_drops_overlap_and_keeps_sections():
    chunks = [
        _chunk(0, "dòng 1\ndòng 2\ndòng 3", "Mục A"),
        _chunk(1, "dòng 3\ndòng 4", "Mục A"),  # "dòng 3" là phần chồng lấp
        _chunk(2, "dòng 5", "Mục B"),
    ]
    text = merge_chunks(chunks)
    assert text.count("dòng 3") == 1
    assert text.index("## Mục A") < text.index("dòng 1") < text.index("## Mục B")


def test_unsupported_numbers_matches_digit_sequences():
    evidence = "Phụ cấp 1.200.000 đồng, nghỉ 12 ngày, lúc 8:30."
    assert unsupported_numbers("Được 1,200,000 đồng [1] và 12 ngày [2], lúc 8:30", evidence) == []
    assert unsupported_numbers("Được nghỉ 15 ngày, 5 lần", evidence) == ["15"]  # bỏ qua số 1 chữ số


def test_clean_rewrite_falls_back_on_garbage():
    assert (
        clean_rewrite('"Thử việc được nghỉ phép không?"\n', "q") == "Thử việc được nghỉ phép không?"
    )
    assert clean_rewrite("Câu hỏi độc lập: Lương thử việc?", "q") == "Lương thử việc?"
    assert clean_rewrite("   ", "gốc") == "gốc"
    assert clean_rewrite("x" * 400, "gốc") == "gốc"


def test_system_prompt_numbers_sources():
    s = [Source(1, "d1", "a.docx", "a", "nội dung A", section="Mục 1"),
         Source(2, "d2", "b.txt", "b", "nội dung B", full=True)]  # fmt: skip
    prompt = build_system_prompt("BASE", s, focus=True)
    assert prompt.startswith("BASE")
    assert "[1] a.docx | mục: Mục 1\nnội dung A" in prompt
    assert "[2] b.txt | toàn văn\nnội dung B" in prompt
    assert "đang trao đổi về đúng các nguồn" in prompt
