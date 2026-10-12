from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = True
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    # Chế độ tập trung: chỉ trả lời dựa trên các tài liệu này (id từ GET /api/documents).
    source_ids: list[str] | None = None
    # False = chat thường, không tìm trong kho tài liệu.
    use_documents: bool = True
    # docs: chat làm việc với tài liệu (không bao giờ dùng tin tức)
    # news: khu Tin tức (chỉ dùng các bài báo đã lấy về)
    scope: Literal["docs", "news"] = "docs"

    @model_validator(mode="after")
    def last_message_is_user(self) -> "ChatRequest":
        if self.messages[-1].role != "user":
            raise ValueError("Tin nhắn cuối cùng phải có role = 'user'")
        return self


class SourceRef(BaseModel):
    n: int
    document_id: str
    document: str
    title: str
    page: int | None = None
    section: str | None = None
    full: bool = False
    score: float | None = None
    url: str | None = None


class ChatStats(BaseModel):
    retrieval_s: float | None = None
    time_to_first_token_s: float | None
    total_s: float
    chunks: int
    approx_tokens_per_s: float | None


class ChatResponse(BaseModel):
    answer: str
    model: str
    stats: ChatStats
    sources: list[SourceRef] = []
    warnings: list[str] = []
