from collections.abc import AsyncIterator

from openai import AsyncOpenAI, OpenAIError

from assistant.core.config import Settings


class LLMError(Exception):
    """LLM server không phản hồi hoặc trả lỗi."""


class LLMClient:
    """Lớp bọc mỏng quanh API chuẩn OpenAI.

    Ollama, llama.cpp server và vLLM đều nói chuyện được qua giao diện này,
    nên đổi runtime chỉ cần đổi LLM_BASE_URL / LLM_MODEL trong .env.
    """

    def __init__(self, settings: Settings) -> None:
        self.model = settings.llm_model
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout_s,
        )

    async def list_models(self) -> list[str]:
        try:
            result = await self._client.models.list()
        except OpenAIError as e:
            raise LLMError(str(e)) from e
        return [m.id for m in result.data]

    async def stream_chat(
        self, messages: list[dict], temperature: float, max_tokens: int
    ) -> AsyncIterator[str]:
        try:
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except OpenAIError as e:
            raise LLMError(str(e)) from e
