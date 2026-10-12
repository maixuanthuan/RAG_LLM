import json

from tests.conftest import FakeLLM

BODY = {"messages": [{"role": "user", "content": "Xin chào"}]}


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_stream_returns_deltas_then_done(make_client):
    r = make_client(FakeLLM()).post("/api/chat", json=BODY)
    assert r.status_code == 200
    events = parse_sse(r.text)
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "Xin chào!"
    assert events[-1]["type"] == "done"
    assert events[-1]["stats"]["chunks"] == 3


def test_non_stream(make_client):
    r = make_client(FakeLLM()).post("/api/chat", json={**BODY, "stream": False})
    assert r.status_code == 200
    assert r.json()["answer"] == "Xin chào!"
    assert r.json()["model"] == "fake-model"


def test_system_prompt_is_prepended(make_client):
    fake = FakeLLM()
    make_client(fake).post("/api/chat", json={**BODY, "stream": False})
    assert fake.last_messages[0]["role"] == "system"
    assert fake.last_messages[-1] == {"role": "user", "content": "Xin chào"}


def test_last_message_must_be_user(make_client):
    bad = {"messages": [{"role": "assistant", "content": "hi"}]}
    assert make_client(FakeLLM()).post("/api/chat", json=bad).status_code == 422


def test_empty_messages_rejected(make_client):
    assert make_client(FakeLLM()).post("/api/chat", json={"messages": []}).status_code == 422


def test_llm_failure_stream_emits_error_event(make_client):
    r = make_client(FakeLLM(fail=True)).post("/api/chat", json=BODY)
    types = [e["type"] for e in parse_sse(r.text)]
    assert "error" in types and types[-1] == "done"


def test_llm_failure_non_stream_returns_502(make_client):
    r = make_client(FakeLLM(fail=True)).post("/api/chat", json={**BODY, "stream": False})
    assert r.status_code == 502
