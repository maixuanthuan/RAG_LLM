from tests.conftest import FakeLLM


def test_liveness(make_client):
    r = make_client(FakeLLM()).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_llm_ready(make_client):
    r = make_client(FakeLLM()).get("/health/llm")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_llm_model_missing(make_client):
    r = make_client(FakeLLM(models=["other"])).get("/health/llm")
    assert r.status_code == 503
    assert r.json()["status"] == "model_missing"


def test_llm_unreachable(make_client):
    r = make_client(FakeLLM(fail=True)).get("/health/llm")
    assert r.status_code == 503
    assert r.json()["status"] == "llm_unreachable"


def test_web_ui_is_served(make_client):
    r = make_client(FakeLLM()).get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "/api/conversations" in r.text and "/api/documents" in r.text
