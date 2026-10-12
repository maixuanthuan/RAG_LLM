import pytest
from fastapi.testclient import TestClient

from assistant.api.deps import get_components, get_conversations, get_llm_client, get_weather
from assistant.core.config import Settings, get_settings
from assistant.main import create_app
from assistant.news.weather import WeatherService
from assistant.rag.factory import build_components
from assistant.services.conversations import ConversationStore
from assistant.services.llm import LLMError
from tests.fakes import WEATHER_SAMPLE, FakeEmbedder


class FakeLLM:
    model = "fake-model"

    def __init__(self, tokens=None, models=None, fail=False, rewrite="câu hỏi viết lại"):
        self.tokens = tokens if tokens is not None else ["Xin ", "chào", "!"]
        self.models = models if models is not None else ["fake-model"]
        self.fail = fail
        self.rewrite = rewrite
        self.last_messages = None
        self.calls: list[list[dict]] = []

    async def list_models(self):
        if self.fail:
            raise LLMError("down")
        return self.models

    async def stream_chat(self, messages, temperature, max_tokens):
        self.calls.append(messages)
        if self.fail:
            raise LLMError("down")
        if "câu hỏi độc lập" in messages[0]["content"].lower():  # lời gọi viết lại câu hỏi
            yield self.rewrite
            return
        self.last_messages = messages
        for t in self.tokens:
            yield t


@pytest.fixture
def components(tmp_path):
    settings = Settings(
        _env_file=None,
        sqlite_path=str(tmp_path / "db" / "a.db"),
        qdrant_path=str(tmp_path / "qdrant"),
        uploads_dir=str(tmp_path / "uploads"),
        chunk_size=600,
        chunk_overlap=100,
        min_relevance=0.0,  # embedding giả cho cosine thấp; test riêng cho ngưỡng
        digest_wait_timeout_s=0.2,  # test không chạy worker nền: không chờ lâu
        digest_wait_poll_s=0.01,
    )
    c = build_components(settings, embedder=FakeEmbedder())
    yield c
    c.close()


@pytest.fixture
def conversations(tmp_path):
    store = ConversationStore(str(tmp_path / "conv.db"))
    yield store
    store.close()


@pytest.fixture
def make_client(components, conversations):
    def _make(fake: FakeLLM, comps=None) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_llm_client] = lambda: fake
        comps = comps or components
        app.dependency_overrides[get_components] = lambda: comps
        app.dependency_overrides[get_settings] = lambda: comps.settings
        app.dependency_overrides[get_conversations] = lambda: conversations
        weather = WeatherService(comps.settings, http_get=lambda url, params: WEATHER_SAMPLE)
        app.dependency_overrides[get_weather] = lambda: weather
        return TestClient(app)

    return _make
