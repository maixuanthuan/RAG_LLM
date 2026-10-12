from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from assistant.api.deps import get_llm_client
from assistant.services.llm import LLMClient, LLMError

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness():
    """Process còn sống (không kiểm tra LLM)."""
    return {"status": "ok"}


@router.get("/health/llm")
async def llm_readiness(llm: LLMClient = Depends(get_llm_client)):
    """LLM server có phản hồi và đã có model cấu hình hay chưa."""
    try:
        models = await llm.list_models()
    except LLMError as e:
        return JSONResponse(status_code=503, content={"status": "llm_unreachable", "error": str(e)})
    if llm.model not in models:
        return JSONResponse(
            status_code=503,
            content={"status": "model_missing", "configured": llm.model, "available": models},
        )
    return {"status": "ok", "model": llm.model, "available": models}
