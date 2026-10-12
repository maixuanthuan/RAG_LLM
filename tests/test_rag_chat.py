import json

import pytest

from assistant.devtools.sample_docs import make_sample_docs
from tests.conftest import FakeLLM


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def of_type(events, kind):
    return [e for e in events if e["type"] == kind]


def ask(client, messages, **extra):
    r = client.post("/api/chat", json={"messages": messages, **extra})
    assert r.status_code == 200
    return parse_sse(r.text)


@pytest.fixture
def kb(components, tmp_path):
    docs = tmp_path / "docs"
    make_sample_docs(docs)
    results = components.pipeline.ingest_path(docs)
    assert all(r.status == "ingested" for r in results)
    return {r.path.split("/")[-1]: r.document_id for r in results}


def test_empty_kb_is_plain_chat(make_client):
    fake = FakeLLM()
    events = ask(make_client(fake), [{"role": "user", "content": "Xin chào"}])
    assert "sources" not in [e["type"] for e in events]
    assert "NGUỒN" not in fake.last_messages[0]["content"]
    assert of_type(events, "debug")[0]["mode"] == "search"


def test_library_mode_sends_sources_and_cites(make_client, kb):
    fake = FakeLLM()
    events = ask(
        make_client(fake), [{"role": "user", "content": "Lương thử việc bằng bao nhiêu %?"}]
    )
    assert of_type(events, "sources")
    docs = [s["document"] for s in of_type(events, "sources")[0]["sources"]]
    assert docs[0] == "Quy_dinh_nhan_su.docx"
    system = fake.last_messages[0]["content"]
    assert "NGUỒN:" in system and "[1] Quy_dinh_nhan_su.docx" in system and "85%" in system
    assert len(fake.calls) == 1  # câu đầu tiên: không cần viết lại


def test_use_documents_false_skips_retrieval(make_client, kb):
    fake = FakeLLM()
    events = ask(make_client(fake), [{"role": "user", "content": "Lương thử việc?"}],
                 use_documents=False)  # fmt: skip
    assert "sources" not in [e["type"] for e in events]


def test_follow_up_is_rewritten_before_retrieval(make_client, kb):
    fake = FakeLLM(rewrite="Hệ số lương làm thêm giờ vào ngày lễ là bao nhiêu?")
    history = [
        {"role": "user", "content": "Làm thêm giờ có cần duyệt không?"},
        {"role": "assistant", "content": "Có, cần quản lý duyệt bằng văn bản."},
        {"role": "user", "content": "Thế ngày lễ thì sao?"},
    ]
    events = ask(make_client(fake), history)
    assert len(fake.calls) == 2  # 1 lần viết lại + 1 lần trả lời
    assert "Thế ngày lễ thì sao?" in fake.calls[0][1]["content"]
    assert "300%" in fake.last_messages[0]["content"]  # tìm bằng câu đã viết lại
    assert fake.last_messages[-1]["content"] == "Thế ngày lễ thì sao?"  # LLM vẫn thấy câu gốc
    assert (
        of_type(events, "debug")[0]["query"] == "Hệ số lương làm thêm giờ vào ngày lễ là bao nhiêu?"
    )
    assert of_type(events, "sources")


def test_focus_mode_puts_full_document_in_prompt(make_client, kb):
    fake = FakeLLM()
    doc_id = kb["Chinh_sach_CNTT.docx"]
    events = ask(make_client(fake), [{"role": "user", "content": "Tóm tắt tài liệu này"}],
                 source_ids=[doc_id])  # fmt: skip
    sources = of_type(events, "sources")[0]["sources"]
    assert [(s["document"], s["full"]) for s in sources] == [("Chinh_sach_CNTT.docx", True)]
    system = fake.last_messages[0]["content"]
    for phrase in ("12 ký tự", "VPN", "24 giờ"):  # toàn văn, không chỉ đoạn liên quan
        assert phrase in system
    assert "Quy_dinh_nhan_su" not in system


def test_focus_mode_too_long_searches_within_pinned_docs(make_client, components, kb):
    components.settings.focus_max_chars = 50
    fake = FakeLLM()
    events = ask(make_client(fake), [{"role": "user", "content": "mật khẩu đổi bao lâu"}],
                 source_ids=[kb["Chinh_sach_CNTT.docx"]])  # fmt: skip
    sources = of_type(events, "sources")[0]["sources"]
    assert sources and all(s["document"] == "Chinh_sach_CNTT.docx" for s in sources)
    assert not any(s["full"] for s in sources)


def test_unknown_focus_id_warns(make_client, kb):
    events = ask(make_client(FakeLLM()), [{"role": "user", "content": "hỏi"}],
                 source_ids=["khong-ton-tai"])  # fmt: skip
    assert any(e["type"] == "warning" and "khong-ton-tai" in e["message"] for e in events)


def test_invented_number_triggers_warning(make_client, kb):
    fake = FakeLLM(tokens=["Lương thử việc bằng ", "85% [1], ", "thử việc tối đa 90 ngày."])
    events = ask(make_client(fake), [{"role": "user", "content": "Lương thử việc?"}])
    warnings = [e["message"] for e in events if e["type"] == "warning"]
    assert len(warnings) == 1 and "90" in warnings[0] and "85" not in warnings[0]


def test_non_stream_returns_sources(make_client, kb):
    r = make_client(FakeLLM()).post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "VPN"}], "stream": False},
    )
    body = r.json()
    assert r.status_code == 200 and body["sources"][0]["n"] == 1


def test_debug_event_reports_mode(make_client, kb):
    client = make_client(FakeLLM())
    msgs = [{"role": "user", "content": "VPN"}]
    assert of_type(ask(client, msgs, use_documents=False), "debug")[0]["mode"] == "none"
    full = ask(client, msgs, source_ids=[kb["Chinh_sach_CNTT.docx"]])
    assert of_type(full, "debug")[0]["mode"] == "full"


def test_relevance_threshold_drops_weak_sources(make_client, components, kb):
    components.settings.min_relevance = 0.99
    fake = FakeLLM()
    events = ask(make_client(fake), [{"role": "user", "content": "xin chào bạn"}])
    assert not of_type(events, "sources")
    debug = of_type(events, "debug")[0]
    assert debug["dropped"] == debug["candidates"] > 0
    assert "NGUỒN" not in fake.last_messages[0]["content"]


def test_threshold_not_applied_to_attached_documents(make_client, components, kb):
    components.settings.min_relevance = 0.99
    components.settings.focus_max_chars = 50  # buộc tìm đoạn trong tài liệu đính kèm
    events = ask(make_client(FakeLLM()), [{"role": "user", "content": "mật khẩu"}],
                 source_ids=[kb["Chinh_sach_CNTT.docx"]])  # fmt: skip
    assert of_type(events, "sources")


def test_prompt_knows_current_date(make_client):
    fake = FakeLLM()
    ask(make_client(fake), [{"role": "user", "content": "hôm nay ngày mấy?"}])
    assert "Thời điểm hiện tại:" in fake.last_messages[0]["content"]


def test_truncated_answer_warns(make_client):
    events = ask(make_client(FakeLLM(tokens=["a"] * 5)), [{"role": "user", "content": "x"}],
                 max_tokens=5)  # fmt: skip
    assert any("bị cắt" in e["message"] for e in of_type(events, "warning"))
    events = ask(make_client(FakeLLM()), [{"role": "user", "content": "x"}])
    assert not of_type(events, "warning")


def test_many_invented_numbers_trigger_strong_warning(make_client, kb):
    fake = FakeLLM(tokens=["Kích thước 120.5 x 102.5 x 42.5 mm, nặng 350g, 2592 x 1944 [1]."])
    events = ask(make_client(fake), [{"role": "user", "content": "Lương thử việc?"}])
    assert any("bịa" in e["message"] for e in of_type(events, "warning"))
