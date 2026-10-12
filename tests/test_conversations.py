import json

from tests.conftest import FakeLLM

TXT = "1. Gửi xe\nNhân viên gửi xe máy miễn phí tại tầng hầm B2.\n".encode()


def sse(r):
    return [json.loads(x[6:]) for x in r.text.splitlines() if x.startswith("data: ")]


def test_conversation_flow_persists_history(make_client):
    fake = FakeLLM()
    client = make_client(fake)
    conv = client.post("/api/conversations").json()
    assert conv["title"] == "Cuộc trò chuyện mới"

    r = client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "Xin chào"})
    assert r.status_code == 200 and sse(r)[-1]["type"] == "done"
    client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "Câu thứ hai"})

    # lần 2 server tự ghép lịch sử: user, assistant, user
    assert [m["role"] for m in fake.last_messages[1:]] == ["user", "assistant", "user"]
    full = client.get(f"/api/conversations/{conv['id']}").json()
    assert full["title"] == "Xin chào"  # tiêu đề lấy từ câu hỏi đầu
    assert [m["content"] for m in full["messages"]] == [
        "Xin chào", "Xin chào!", "Câu thứ hai", "Xin chào!",
    ]  # fmt: skip
    assert "stats" in full["messages"][1]["meta"] and "debug" in full["messages"][1]["meta"]
    assert client.get("/api/conversations").json()[0]["id"] == conv["id"]


def test_attached_document_focuses_answers_and_is_forgotten_on_delete(make_client):
    fake = FakeLLM()
    client = make_client(fake)
    doc = client.post("/api/documents", files={"file": ("Gui_xe.txt", TXT)}).json()
    conv = client.post("/api/conversations", json={"source_ids": [doc["document_id"]]}).json()

    r = client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "tóm tắt"})
    sources = [e for e in sse(r) if e["type"] == "sources"][0]["sources"]
    assert sources[0]["document"] == "Gui_xe.txt" and sources[0]["full"]
    assert "tầng hầm B2" in fake.last_messages[0]["content"]

    client.delete(f"/api/documents/{doc['document_id']}")
    assert client.get(f"/api/conversations/{conv['id']}").json()["source_ids"] == []


def test_patch_and_delete(make_client):
    client = make_client(FakeLLM())
    cid = client.post("/api/conversations").json()["id"]
    r = client.patch(f"/api/conversations/{cid}", json={"title": "Đổi tên", "source_ids": ["a"]})
    assert r.json()["title"] == "Đổi tên" and r.json()["source_ids"] == ["a"]
    assert client.delete(f"/api/conversations/{cid}").status_code == 200
    assert client.get(f"/api/conversations/{cid}").status_code == 404
    assert (
        client.post(f"/api/conversations/{cid}/messages", json={"content": "x"}).status_code == 404
    )


# ---- nhiều tài liệu trong một cuộc trò chuyện ----
from types import SimpleNamespace  # noqa: E402

from assistant.rag.grounding import pick_documents  # noqa: E402


def _d(name):
    return SimpleNamespace(filename=name)


def test_pick_documents_rules():
    docs = [_d("BOM CAM AI P_X.docx"), _d("Quy_dinh_nhan_su.docx"), _d("Bao_cao_Q3.pdf")]
    assert pick_documents("file BOM có bao nhiêu camera?", docs)[0] == [docs[0]]
    assert pick_documents("quy định nhân sự nói gì về phép?", docs)[0] == [docs[1]]
    assert pick_documents("tóm tắt file này", docs)[0] == [docs[2]]  # mới nhất
    assert pick_documents("tài liệu vừa gửi nói gì", docs)[0] == [docs[2]]
    assert pick_documents("so sánh các tài liệu", docs)[0] == docs
    assert pick_documents("tóm tắt file này", docs[:1])[0] == docs[:1]


def _upload(client, name, text):
    return client.post("/api/documents", files={"file": (name, text.encode())}).json()


def test_attachments_are_timeline_messages_and_this_file_means_latest(make_client):
    fake = FakeLLM()
    client = make_client(fake)
    a = _upload(client, "Gui_xe.txt", "1. Gửi xe\nGửi xe tại tầng hầm B2.\n")
    b = _upload(client, "Wifi.txt", "1. Wifi\nMật khẩu wifi khách là abc123.\n")
    cid = client.post("/api/conversations").json()["id"]

    client.post(f"/api/conversations/{cid}/attachments", json={"document_id": a["document_id"]})
    client.post(f"/api/conversations/{cid}/messages", json={"content": "file này nói gì?"})
    assert "tầng hầm B2" in fake.last_messages[0]["content"]
    assert fake.last_messages[-1]["content"].startswith("[Đính kèm: Gui_xe.txt]")

    conv = client.post(
        f"/api/conversations/{cid}/attachments", json={"document_id": b["document_id"]}
    ).json()
    assert conv["source_ids"] == [a["document_id"], b["document_id"]]
    assert [m["role"] for m in conv["messages"]] == ["file", "user", "assistant", "file"]

    r = client.post(f"/api/conversations/{cid}/messages", json={"content": "file này nói gì?"})
    system = fake.last_messages[0]["content"]
    assert "abc123" in system and "tầng hầm B2" not in system  # chỉ tài liệu mới nhất
    assert "cũ → mới): Gui_xe.txt, Wifi.txt" in system
    debug = [e for e in sse(r) if e["type"] == "debug"][0]
    assert debug["documents"] == ["Wifi.txt"] and "gần nhất" in debug["pick"]

    client.post(f"/api/conversations/{cid}/messages", json={"content": "so sánh hai tài liệu"})
    system = fake.last_messages[0]["content"]
    assert "abc123" in system and "tầng hầm B2" in system  # không nói rõ -> dùng tất cả

    r = client.delete(f"/api/conversations/{cid}/attachments/{a['document_id']}")
    assert r.json()["source_ids"] == [b["document_id"]]
    client.post(f"/api/conversations/{cid}/messages", json={"content": "tóm tắt"})
    system = fake.last_messages[0]["content"]
    assert "đang đính kèm (cũ → mới): Wifi.txt." in system and "đã bị gỡ" in system
    assert "tầng hầm B2" not in system


def test_attach_unknown_document_404(make_client):
    client = make_client(FakeLLM())
    cid = client.post("/api/conversations").json()["id"]
    r = client.post(f"/api/conversations/{cid}/attachments", json={"document_id": "khong-co"})
    assert r.status_code == 404
