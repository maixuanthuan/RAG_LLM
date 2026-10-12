import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from assistant.api.deps import get_chat_service
from assistant.schemas.chat import ChatRequest
from assistant.services.chat import ChatService

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat")
async def chat(req: ChatRequest, service: ChatService = Depends(get_chat_service)):
    if not req.stream:
        return await service.complete(req)

    async def sse():
        async for event in service.stream(req):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
