import asyncio
import json

import pytest

from assistant.rag.db import ChunkRecord
from assistant.rag.digest import classify_question, is_empty_extract, split_parts
from assistant.schemas.chat import ChatRequest
from assistant.services.chat import ChatService
from assistant.services.digest import DigestWorker
from tests.conftest import FakeLLM


def long_doc() -> bytes:
    """~20 mục x ~800 ký tự = ~16.000 ký tự: dài hơn FOCUS_MAX_CHARS (12.000)."""
    parts = []
    for i in range(1, 21):
        body = " ".join(f"Nội dung mục {i} câu {j} về chủ đề số {i}." for j in range(1, 25))
        parts.append(f"{i}. Mục số {i}\n{body}\n")
    return "\n".join(parts).encode()


def sse(r):
    return [json.loads(x[6:]) for x in r.text.splitlines() if x.startswith("data: ")]


def of_type(events, kind):
    return [e for e in events if e["type"] == kind]


@pytest.fixture
def long_doc_id(make_client, components):
    components.settings.digest_part_chars = 3000
    r = make_client(FakeLLM()).post("/api/documents", files={"file": ("dai.txt", long_doc())})
    assert r.status_code == 200
    return r.json()["document_id"]


def chat(client, doc_id, question):
    conv = client.post("/api/conversations", json={"source_ids": [doc_id]}).json()
    return sse(client.post(f"/api/conversations/{conv['id']}/messages", json={"content": question}))


def run_digest(components, fake=None):
    worker = DigestWorker(fake or FakeLLM(), components.settings, components)
    while asyncio.run(worker.run_once()):
        pass


# ---- chia phần ----
def _c(ord_, text, page=None, section=None):
    return ChunkRecord(str(ord_), "d", "a.pdf", "a", ord_, text, page, section, "x")


def test_split_parts_keeps_every_line_and_pages():
    chunks = [_c(i, f"dòng {i} " + "x" * 300, page=i // 3 + 1) for i in range(30)]
    parts = split_parts(chunks, 2000)
    joined = "\n".join(p.text for p in parts)
    assert all(f"dòng {i} " in joined for i in range(30))  # không sót dòng nào
    assert len(parts) > 3 and all(len(p.text) <= 2000 + 400 for p in parts)
    assert parts[0].page_from == 1 and parts[-1].page_to == 10
    assert all(p.text.startswith("[tr.") for p in parts)  # mỗi phần biết mình ở trang nào


def test_classify_question():
    assert classify_question("Tóm tắt tài liệu này") == "overview"
    assert classify_question("tai lieu noi ve gi") == "overview"
    assert classify_question("Liệt kê tất cả ví dụ trong tài liệu") == "scan"
    assert classify_question("chương 3 nói gì về dữ liệu?") == "specific"


def test_is_empty_extract():
    assert is_empty_extract("KHÔNG CÓ") and is_empty_extract("Không có.") and is_empty_extract("")
    assert not is_empty_extract("Không có ví dụ cụ thể nhưng có định nghĩa AI (tr. 3)")


# ---- đọc trước ----
def test_upload_marks_long_document_pending_and_short_skip(make_client, components, long_doc_id):
    docs = {d["filename"]: d for d in make_client(FakeLLM()).get("/api/documents").json()}
    assert docs["dai.txt"]["digest"]["status"] == "pending"
    short = make_client(FakeLLM()).post(
        "/api/documents", files={"file": ("ngan.txt", "1. A\nNgắn.\n".encode())}
    )
    assert components.db.get_digest(short.json()["document_id"]).status == "skip"


def test_worker_reads_every_part_then_builds_overview(components, long_doc_id):
    fake = FakeLLM(tokens=["- ghi chú chi tiết " * 120])  # ghi chú dài -> buộc phải gộp nhiều cấp
    run_digest(components, fake)
    state = components.db.get_digest(long_doc_id)
    assert state.status == "done" and state.parts_done == state.parts_total > 3
    levels = components.db.note_levels(long_doc_id)
    assert levels[0] == 0 and 1 in levels and len(levels) >= 3  # tổng quan + phần + bản gộp
    assert len(components.db.get_notes(long_doc_id, 1)) == state.parts_total
    # mỗi phần được đưa nguyên văn cho AI: mục 1 và mục 20 đều xuất hiện trong các lần gọi
    seen = "\n".join(m[1]["content"] for m in fake.calls)
    assert "Mục số 1\n" in seen and "Mục số 20" in seen


def test_reupload_resets_digest(make_client, components, long_doc_id):
    run_digest(components)
    make_client(FakeLLM()).post("/api/documents", files={"file": ("dai.txt", long_doc() + b"x")})
    assert components.db.get_digest(long_doc_id).status == "pending"
    assert components.db.note_levels(long_doc_id) == []


# ---- hỏi về tài liệu dài ----
def test_overview_question_uses_notes_of_all_parts(make_client, components, long_doc_id):
    run_digest(components)
    fake = FakeLLM()
    events = chat(make_client(fake), long_doc_id, "Tóm tắt tài liệu này")
    assert of_type(events, "debug")[0]["mode"] == "overview"
    labels = [s["section"] for s in of_type(events, "sources")[0]["sources"]]
    total = components.db.get_digest(long_doc_id).parts_total
    assert labels[0] == "Tổng quan toàn tài liệu"
    assert len([x for x in labels if x.startswith("Ghi chú phần")]) == total


def test_question_waits_until_document_fully_read(components, long_doc_id):
    """Nguyên tắc: đọc xong toàn bộ rồi mới trả lời (worker chạy song song với câu hỏi)."""
    components.settings.digest_wait_timeout_s = 30
    service = ChatService(FakeLLM(), components.settings, components)
    worker = DigestWorker(FakeLLM(), components.settings, components)
    req = ChatRequest(
        messages=[{"role": "user", "content": "Tóm tắt tài liệu này"}], source_ids=[long_doc_id]
    )

    async def main():
        task = asyncio.create_task(_collect(service.stream(req)))
        await asyncio.sleep(0.05)  # câu hỏi đến trước, lúc tài liệu chưa được đọc
        while await worker.run_once():
            await asyncio.sleep(0.02)
        return await task

    events = asyncio.run(main())
    assert any("đọc" in e["message"] for e in of_type(events, "progress"))
    assert of_type(events, "debug")[0]["mode"] == "overview"  # trả lời bằng ghi chú mọi phần
    assert components.db.get_digest(long_doc_id).status == "done"


def test_wait_timeout_falls_back_to_search_with_warning(make_client, long_doc_id):
    events = chat(make_client(FakeLLM()), long_doc_id, "Tóm tắt tài liệu này")
    assert of_type(events, "debug")[0]["mode"] == "search"
    assert any("Hết thời gian chờ" in e["message"] for e in of_type(events, "warning"))


def test_question_prioritizes_its_document(components, make_client, long_doc_id):
    other = make_client(FakeLLM()).post(
        "/api/documents", files={"file": ("khac.txt", long_doc().replace(b"M", b"N"))}
    )
    components.db.prioritize_digests([other.json()["document_id"]])
    assert components.db.next_pending_digest() == other.json()["document_id"]


async def _collect(agen):
    return [e async for e in agen]


def test_scan_question_reads_every_part(make_client, components, long_doc_id):
    fake = FakeLLM()
    events = chat(make_client(fake), long_doc_id, "Liệt kê tất cả các mục trong tài liệu")
    progress = of_type(events, "progress")
    parts = [m for m in fake.calls if "Câu hỏi:" in m[-1]["content"]]
    assert len(parts) >= 4 and len([p for p in progress if "Đang đọc" in p["message"]]) == len(
        parts
    )
    assert of_type(events, "debug")[0]["mode"] == "scan"
    assert of_type(events, "sources")


def test_scan_with_nothing_found_says_so(make_client, long_doc_id):
    fake = FakeLLM(tokens=["KHÔNG CÓ"])
    events = chat(make_client(fake), long_doc_id, "Liệt kê tất cả giá ChatGPT trong tài liệu")
    sources = of_type(events, "sources")[0]["sources"]
    assert len(sources) == 1 and sources[0]["section"] == "Kết quả quét toàn bộ"
    assert "không phần nào có thông tin" in fake.last_messages[0]["content"]


def test_specific_question_searches_with_neighbors_and_overview(
    make_client, components, long_doc_id
):
    run_digest(components)
    fake = FakeLLM()
    events = chat(make_client(fake), long_doc_id, "mục số 7 nói về chủ đề gì")
    assert of_type(events, "debug")[0]["mode"] == "search"
    sources = of_type(events, "sources")[0]["sources"]
    assert sources[0]["section"] == "Tổng quan toàn tài liệu"
    assert "chủ đề số 7" in fake.last_messages[0]["content"]


def test_documents_api_reports_digest_progress(make_client, components, long_doc_id):
    run_digest(components)
    d = make_client(FakeLLM()).get("/api/documents").json()[0]["digest"]
    assert d["status"] == "done" and d["parts_done"] == d["parts_total"]


def test_delete_document_removes_digest(make_client, components, long_doc_id):
    run_digest(components)
    make_client(FakeLLM()).delete(f"/api/documents/{long_doc_id}")
    assert components.db.get_digest(long_doc_id) is None
    assert components.db.note_levels(long_doc_id) == []
