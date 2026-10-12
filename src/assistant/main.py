import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from assistant.api.deps import get_components, get_conversations, get_llm_client
from assistant.api.routes import chat, conversations, documents, health, news
from assistant.core.config import get_settings
from assistant.core.logging import setup_logging
from assistant.rag.reindex import ensure_index
from assistant.services.digest import DigestWorker
from assistant.services.llm import LLMError
from assistant.services.news import NewsWorker

WEB_DIR = Path(__file__).parent / "web"


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Đổi model embedding / chép data sang máy mới: dựng lại kho vector trước khi phục vụ
        await run_in_threadpool(ensure_index, get_components())
        tasks = []
        if settings.digest_enabled:  # AI đọc trước tài liệu dài, chạy nền
            dw = DigestWorker(get_llm_client(), settings, get_components())
            tasks.append(asyncio.create_task(dw.run_forever()))
        if settings.news_enabled:  # lấy tin + tóm tắt, chạy nền
            app.state.news_worker = NewsWorker(get_llm_client(), settings, get_components())
            tasks.append(asyncio.create_task(app.state.news_worker.run_forever()))
        yield
        for t in tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        if get_components.cache_info().currsize:  # chỉ đóng nếu đã được tạo
            get_components().close()
        if get_conversations.cache_info().currsize:
            get_conversations().close()

    app = FastAPI(title="Internal AI Assistant", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(documents.router)
    app.include_router(conversations.router)
    app.include_router(news.router)

    @app.get("/", include_in_schema=False)
    async def web_ui():
        """Giao diện web: một file HTML tĩnh, gọi các API ở trên."""
        return FileResponse(WEB_DIR / "index.html")

    @app.exception_handler(LLMError)
    async def llm_error_handler(_: Request, exc: LLMError):
        return JSONResponse(status_code=502, content={"detail": f"LLM error: {exc}"})

    logging.getLogger(__name__).info(
        "started env=%s llm=%s model=%s",
        settings.app_env,
        settings.llm_base_url,
        settings.llm_model,
    )
    return app


app = create_app()
