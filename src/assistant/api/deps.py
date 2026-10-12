from functools import lru_cache

from fastapi import Depends

from assistant.core.config import Settings, get_settings
from assistant.news.weather import WeatherService
from assistant.rag.factory import Components, build_components
from assistant.services.chat import ChatService
from assistant.services.conversations import ConversationStore
from assistant.services.llm import LLMClient


@lru_cache
def get_llm_client() -> LLMClient:
    return LLMClient(get_settings())


@lru_cache
def get_components() -> Components:
    """Một bộ SQLite + Qdrant + embedder dùng chung cho cả process API."""
    return build_components(get_settings())


@lru_cache
def get_conversations() -> ConversationStore:
    """Lịch sử hội thoại, lưu chung file SQLite với kho tài liệu."""
    return ConversationStore(get_settings().sqlite_path)


@lru_cache
def get_weather() -> WeatherService:
    return WeatherService(get_settings())


def get_chat_service(
    llm: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_settings),
    components: Components = Depends(get_components),
    weather: WeatherService = Depends(get_weather),
) -> ChatService:
    return ChatService(llm, settings, components, weather)
