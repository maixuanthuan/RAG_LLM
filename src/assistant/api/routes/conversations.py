"""Hội thoại có lưu lịch sử (dùng cho giao diện web).

Client chỉ gửi câu hỏi mới; server tự ghép lịch sử và tài liệu đính kèm của cuộc trò chuyện,
gọi ChatService như /api/chat, rồi lưu câu trả lời kèm nguồn và thông tin debug.

Tài liệu đính kèm là một tin nhắn (role="file") nằm đúng chỗ trong dòng thời gian. Một cuộc
trò chuyện có thể đính kèm nhiều tài liệu ở nhiều thời điểm; source_ids giữ thứ tự đính kèm
(cuối danh sách = mới nhất) để bot hiểu "file này" là file nào.
"""

import json
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from assistant.api.deps import get_chat_service, get_components, get_conversations
from assistant.rag.factory import Components
from assistant.schemas.chat import ChatRequest
from assistant.services.chat import ChatService
from assistant.services.conversations import NEW_TITLE, ConversationStore

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    source_ids: list[str] | None = None


class NewMessage(BaseModel):
    content: str = Field(min_length=1)


class Attachment(BaseModel):
    document_id: str


def build_history(messages) -> list[dict]:
    """Lịch sử cho LLM: tin nhắn file được ghi vào đầu câu hỏi kế tiếp, ví dụ
    "[Đính kèm: BOM.docx]", để LLM biết tài liệu nào được đưa vào lúc nào."""
    history: list[dict] = []
    pending: list[str] = []
    for m in messages:
        if m.role == "file":
            pending.append(m.content)
        elif m.content.strip():
            content = m.content
            if m.role == "user" and pending:
                content = f"[Đính kèm: {', '.join(pending)}]\n{content}"
                pending = []
            history.append({"role": m.role, "content": content})
    return history


def _get(store: ConversationStore, cid: str):
    conv = store.get(cid)
    if conv is None:
        raise HTTPException(404, "Không có cuộc trò chuyện này")
    return conv


@router.get("")
def list_conversations(store: ConversationStore = Depends(get_conversations)):
    return [asdict(c) for c in store.list_all()]


@router.post("")
def create_conversation(
    body: ConversationPatch | None = None, store: ConversationStore = Depends(get_conversations)
):
    body = body or ConversationPatch()
    return asdict(store.create(body.title or NEW_TITLE, body.source_ids))


@router.get("/{cid}")
def get_conversation(cid: str, store: ConversationStore = Depends(get_conversations)):
    conv = _get(store, cid)
    return {**asdict(conv), "messages": [asdict(m) for m in store.messages(cid)]}


@router.patch("/{cid}")
def patch_conversation(
    cid: str, body: ConversationPatch, store: ConversationStore = Depends(get_conversations)
):
    _get(store, cid)
    return asdict(store.update(cid, body.title, body.source_ids))


@router.delete("/{cid}")
def delete_conversation(cid: str, store: ConversationStore = Depends(get_conversations)):
    if not store.delete(cid):
        raise HTTPException(404, "Không có cuộc trò chuyện này")
    return {"deleted": cid}


@router.post("/{cid}/attachments")
def attach_document(
    cid: str,
    body: Attachment,
    store: ConversationStore = Depends(get_conversations),
    c: Components = Depends(get_components),
):
    """Đính kèm tài liệu (đã nạp) vào cuộc trò chuyện: thêm thẻ file vào dòng thời gian."""
    conv = _get(store, cid)
    with c.lock:
        doc = c.db.get_document_by_id(body.document_id)
        art = c.db.get_articles([body.document_id]).get(body.document_id) if doc else None
    if doc is None:
        raise HTTPException(404, "Không có tài liệu này")
    name = f"📰 {art.title}" if art else doc.filename  # bài báo: hiện tiêu đề thay tên file
    ids = [x for x in conv.source_ids if x != doc.id] + [doc.id]  # đưa lên mới nhất
    store.update(cid, source_ids=ids)
    store.add_message(cid, "file", name, {"document_id": doc.id, "url": art.url if art else None})
    return {**asdict(store.get(cid)), "messages": [asdict(m) for m in store.messages(cid)]}


@router.delete("/{cid}/attachments/{document_id}")
def detach_document(
    cid: str, document_id: str, store: ConversationStore = Depends(get_conversations)
):
    """Gỡ tài liệu khỏi cuộc trò chuyện (thẻ file vẫn còn trong lịch sử, hiện là đã gỡ)."""
    conv = _get(store, cid)
    return asdict(store.update(cid, source_ids=[x for x in conv.source_ids if x != document_id]))


@router.post("/{cid}/messages")
async def send_message(
    cid: str,
    body: NewMessage,
    store: ConversationStore = Depends(get_conversations),
    service: ChatService = Depends(get_chat_service),
):
    """Gửi câu hỏi, trả SSE giống /api/chat. Câu hỏi và câu trả lời được lưu vào lịch sử."""
    conv = _get(store, cid)
    store.add_message(cid, "user", body.content)
    if conv.title == NEW_TITLE and conv.kind == "chat":
        title = " ".join(body.content.split())
        store.update(cid, title=title[:60] + ("…" if len(title) > 60 else ""))
    history = build_history(store.messages(cid))
    req = ChatRequest(
        messages=history,
        source_ids=conv.source_ids or None,
        scope="news" if conv.kind == "news" else "docs",
    )

    async def sse():
        parts: list[str] = []
        meta: dict = {"sources": [], "warnings": []}
        async for ev in service.stream(req):
            kind = ev["type"]
            if kind == "delta":
                parts.append(ev["text"])
            elif kind == "sources":
                meta["sources"] = ev["sources"]
            elif kind == "warning":
                meta["warnings"].append(ev["message"])
            elif kind == "error":
                meta["error"] = ev["message"]
            elif kind == "debug":
                meta["debug"] = {k: v for k, v in ev.items() if k != "type"}
            elif kind == "done":
                meta["stats"] = ev["stats"]
                store.add_message(cid, "assistant", "".join(parts), meta)
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
