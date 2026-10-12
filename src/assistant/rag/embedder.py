"""Embedding qua API chuẩn OpenAI (/v1/embeddings): Ollama, vLLM, ... đều dùng được."""

from typing import Protocol

from openai import OpenAI, OpenAIError

from assistant.core.config import Settings


class EmbeddingError(Exception):
    pass


class Embedder(Protocol):
    model: str

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OpenAICompatEmbedder:
    def __init__(self, settings: Settings) -> None:
        self.model = settings.embed_model
        self._batch = max(1, settings.embed_batch_size)
        self._query_prefix = settings.embed_query_prefix
        self._passage_prefix = settings.embed_passage_prefix
        self._client = OpenAI(base_url=settings.embed_url, api_key=settings.embed_key, timeout=120)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = self._client.embeddings.create(model=self.model, input=texts)
        except OpenAIError as e:
            raise EmbeddingError(
                f"Gọi embedding thất bại ({e}). Kiểm tra server đang chạy và model "
                f"'{self.model}' đã được kéo về (ollama pull {self.model})."
            ) from e
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            batch = [self._passage_prefix + t for t in texts[i : i + self._batch]]
            out.extend(self._embed(batch))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self._query_prefix + text])[0]
