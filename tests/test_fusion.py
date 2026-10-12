from assistant.rag.db import build_fts_query
from assistant.rag.fusion import rrf


def test_rrf_prefers_items_found_by_both():
    fused = rrf({"v": ["a", "b", "c"], "k": ["b", "d"]}, k=60)
    assert fused[0][0] == "b"
    assert {i for i, _ in fused} == {"a", "b", "c", "d"}


def test_fts_query_drops_stopwords_and_adds_phrases():
    q = build_fts_query("Nhân viên thử việc có được nghỉ phép không?")
    assert '"nhan"' in q and '"viec"' in q and '"phep"' in q
    assert '"thu viec"' in q or '"nhan vien"' in q
    assert '"co"' not in q and '"khong"' not in q


def test_fts_query_empty():
    assert build_fts_query("là và của ?") is None
