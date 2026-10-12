import json
from pathlib import Path

import pytest

from assistant.core.config import Settings
from assistant.devtools.sample_docs import make_sample_docs
from assistant.ingestion.text import fold
from assistant.rag.factory import build_components
from tests.fakes import FakeEmbedder

GOLDEN = Path(__file__).resolve().parent.parent / "eval" / "golden_sample.jsonl"


@pytest.fixture
def comp(tmp_path):
    settings = Settings(
        _env_file=None,
        sqlite_path=str(tmp_path / "db" / "a.db"),
        qdrant_path=str(tmp_path / "qdrant"),
        chunk_size=600,
        chunk_overlap=100,
    )
    c = build_components(settings, embedder=FakeEmbedder())
    yield c
    c.close()


@pytest.fixture
def docs(tmp_path):
    root = tmp_path / "docs"
    make_sample_docs(root)
    return root


def statuses(results):
    return {r.path: r.status for r in results}


def test_ingest_directory_infers_departments(comp, docs):
    results = comp.pipeline.ingest_path(docs)
    assert all(r.status == "ingested" for r in results), [r.error for r in results]
    assert len(results) == 5
    stats = comp.db.stats()
    assert stats["documents"] == 5
    assert stats["chunks"] == comp.vectors.count() > 5
    deps = {r["department"]: r["docs"] for r in stats["by_department"]}
    assert deps == {"HR": 3, "Finance": 1, "IT": 1}


def test_reingest_is_idempotent_and_detects_changes(comp, docs):
    comp.pipeline.ingest_path(docs)
    before = comp.vectors.count()
    assert {r.status for r in comp.pipeline.ingest_path(docs)} == {"skipped"}

    target = docs / "HR" / "Huong_dan_onboarding.txt"
    target.write_text("1. Mục mới\nNội dung hoàn toàn mới về chế độ gửi xe.\n", "utf-8")
    res = {r.path: r for r in comp.pipeline.ingest_path(docs)}
    assert res["HR/Huong_dan_onboarding.txt"].status == "replaced"
    assert comp.vectors.count() < before  # bản cũ có nhiều chunk hơn bị thay thế
    assert comp.vectors.count() == comp.db.stats()["chunks"]  # không còn vector mồ côi
    hits = comp.retriever.search("gửi xe", mode="keyword")
    assert hits and hits[0].document == "Huong_dan_onboarding.txt"
    assert not comp.retriever.search("Laptop nhận ở phòng IT tầng 3", mode="keyword")[:0]


def test_keyword_search_is_accent_insensitive(comp, docs):
    comp.pipeline.ingest_path(docs)
    with_accents = comp.retriever.search("hoàn ứng công tác", mode="keyword")
    without = comp.retriever.search("hoan ung cong tac", mode="keyword")
    assert [h.chunk_id for h in with_accents] == [h.chunk_id for h in without]
    assert with_accents[0].document == "Quy_trinh_thanh_toan.docx"


def test_department_filter_applies_to_every_mode(comp, docs):
    comp.pipeline.ingest_path(docs)
    for mode in ("vector", "keyword", "hybrid"):
        hits = comp.retriever.search("mật khẩu phải đổi bao lâu", 10, mode, ["HR"])
        assert all(h.department == "HR" for h in hits), mode
    hits = comp.retriever.search("mật khẩu phải đổi bao lâu", 3, "hybrid", ["IT"])
    assert hits[0].document == "Chinh_sach_CNTT.docx"


def test_hits_carry_citation_metadata(comp, docs):
    comp.pipeline.ingest_path(docs)
    hit = comp.retriever.search("Thời gian thử việc tối đa là bao lâu", 1, "hybrid")[0]
    assert hit.document == "Quy_dinh_nhan_su.docx"
    assert hit.section and "Thử việc" in hit.section
    assert hit.department == "HR"
    assert "60 ngày" in hit.text


def test_changing_embedding_model_requires_reset(comp, docs):
    comp.pipeline.ingest_path(docs)

    class OtherModel(FakeEmbedder):
        model = "another-model"

    comp.pipeline._embedder = OtherModel()
    (docs / "HR" / "extra.txt").write_text("1. Mục\nNội dung thêm.\n", "utf-8")
    res = {r.path: r for r in comp.pipeline.ingest_path(docs)}
    assert res["HR/extra.txt"].status == "failed"
    assert "--reset" in res["HR/extra.txt"].error


def test_failed_file_does_not_break_batch(comp, docs):
    (docs / "HR" / "hong.docx").write_bytes(b"khong phai docx")
    res = {r.path: r for r in comp.pipeline.ingest_path(docs)}
    assert res["HR/hong.docx"].status == "failed" and res["HR/hong.docx"].error
    assert sum(1 for r in res.values() if r.status == "ingested") == 5


def test_golden_set_quality(comp, docs):
    """Hồi quy chất lượng truy xuất với embedding giả (thiên về từ vựng)."""
    comp.pipeline.ingest_path(docs)
    items = [json.loads(line) for line in GOLDEN.read_text("utf-8").splitlines() if line.strip()]

    def hit_at(mode: str, k: int) -> float:
        ok = 0
        for item in items:
            hits = comp.retriever.search(item["question"], k, mode)
            ok += any(
                h.document in item["documents"]
                and any(fold(n) in fold(h.text) for n in item.get("contains", [""]))
                for h in hits
            )
        return ok / len(items)

    assert hit_at("keyword", 5) >= 0.8
    assert hit_at("hybrid", 5) >= 0.8
