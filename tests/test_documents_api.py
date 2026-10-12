from pathlib import Path

from tests.conftest import FakeLLM

TXT = "1. Gửi xe\nNhân viên gửi xe máy miễn phí tại tầng hầm B2.\n".encode()


def test_upload_list_ask_delete(make_client, components):
    client = make_client(FakeLLM())
    r = client.post("/api/documents", files={"file": ("Gui_xe.txt", TXT)}, data={"group": "HR"})
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["status"] == "ingested" and doc["path"] == "HR/Gui_xe.txt"
    assert (Path(components.settings.uploads_dir) / "HR" / "Gui_xe.txt").is_file()

    listed = client.get("/api/documents").json()
    assert [(d["id"], d["group"]) for d in listed] == [(doc["document_id"], "HR")]

    fake = FakeLLM()
    client2 = make_client(fake)
    client2.post("/api/chat", json={"messages": [{"role": "user", "content": "gửi xe ở đâu"}]})
    assert "tầng hầm B2" in fake.last_messages[0]["content"]

    r = client.delete(f"/api/documents/{doc['document_id']}")
    assert r.status_code == 200
    assert client.get("/api/documents").json() == []
    assert components.vectors.count() == 0
    assert not (Path(components.settings.uploads_dir) / "HR" / "Gui_xe.txt").exists()
    assert client.delete(f"/api/documents/{doc['document_id']}").status_code == 404


def test_reupload_same_name_replaces(make_client):
    client = make_client(FakeLLM())
    client.post("/api/documents", files={"file": ("a.txt", TXT)})
    r = client.post("/api/documents", files={"file": ("a.txt", "1. Mới\nNội dung mới.\n".encode())})
    assert r.json()["status"] == "replaced"
    assert len(client.get("/api/documents").json()) == 1


def test_upload_rejects_bad_input(make_client, components):
    client = make_client(FakeLLM())
    assert client.post("/api/documents", files={"file": ("x.exe", b"MZ")}).status_code == 400
    r = client.post("/api/documents", files={"file": ("a.txt", TXT)}, data={"group": "../etc"})
    assert r.status_code == 400
    components.settings.max_upload_mb = 0
    assert client.post("/api/documents", files={"file": ("a.txt", TXT)}).status_code == 413


def test_broken_file_returns_422(make_client):
    client = make_client(FakeLLM())
    r = client.post("/api/documents", files={"file": ("hong.docx", b"khong phai docx")})
    assert r.status_code == 422 and "hong.docx" in r.json()["detail"]
