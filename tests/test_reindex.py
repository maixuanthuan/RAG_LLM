"""Dựng lại kho vector từ SQLite: đổi model embedding / chép data sang server mới."""

from assistant.rag.reindex import ensure_index, needs_reindex


def _ingest(components, tmp_path):
    f = tmp_path / "docs" / "a.md"
    f.parent.mkdir()
    f.write_text("# Quy trình\n\nNhân viên nghỉ phép phải báo trước 3 ngày.\n", encoding="utf-8")
    assert components.pipeline.ingest_file(f, f.parent).status == "ingested"


def test_nothing_to_do_when_index_matches(components, tmp_path):
    assert needs_reindex(components) is None  # chưa có dữ liệu
    _ingest(components, tmp_path)
    assert needs_reindex(components) is None


def test_rebuilds_empty_vector_store(components, tmp_path):
    """Chép data/ sang máy mới: SQLite còn, Qdrant trống -> tự dựng lại, tìm kiếm chạy."""
    _ingest(components, tmp_path)
    n = components.db.count_chunks()
    components.vectors.reset()
    assert "thiếu" in needs_reindex(components)
    ensure_index(components)
    assert components.vectors.count() == n
    assert needs_reindex(components) is None
    hits = components.retriever.search("nghỉ phép báo trước mấy ngày")
    assert hits and hits[0].vector_score is not None


def test_rebuilds_when_embedding_model_changes(components, tmp_path):
    _ingest(components, tmp_path)
    components.db.set_meta("embed_model", "bge-m3-cpu")  # dữ liệu tạo trên máy dev
    assert "đổi model" in needs_reindex(components)
    ensure_index(components)
    assert components.db.get_meta("embed_model") == components.embedder.model
