"""Khu Tin tức: danh sách bài, trạng thái lấy tin, lấy tin ngay, hội thoại tin tức.

Hỏi đáp dùng chung /api/conversations/{id}/messages: hội thoại loại "news" tự động chỉ
tìm trong bài báo (scope="news"), hội thoại khu chat không bao giờ thấy bài báo.
"""

from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request

from assistant.api.deps import get_components, get_conversations, get_weather
from assistant.news.brief import ago
from assistant.news.events import group_events, n_sources, representative
from assistant.rag.factory import Components
from assistant.services.conversations import ConversationStore

router = APIRouter(prefix="/api/news", tags=["news"])

NEWS_TITLE = "Tin tức"


@router.get("")
def list_news(
    category: str | None = None, limit: int = 120, c: Components = Depends(get_components)
):
    """Danh sách SỰ KIỆN (các bài cùng đưa một tin gộp làm một), mới nhất trước."""
    cats = {**c.settings.news_feeds, **c.settings.news_topics}
    now = datetime.now(ZoneInfo(c.settings.timezone))
    with c.lock:
        arts = c.db.list_articles(category=category, limit=min(limit * 3, 1500))
    out = []
    for g in group_events(arts)[:limit]:
        a = representative(g)
        out.append(
            {
                "document_id": a.document_id,
                "url": a.url,
                "source": a.source,
                "category": a.category,
                "category_label": cats.get(a.category, {}).get("label", a.category),
                "tags": [t for t in (a.tags or "").split(",") if t],
                "title": a.title,
                "summary": a.summary,
                "lead": a.lead,
                "summary_status": a.summary_status,
                "published_at": g[0].published_at,
                "ago": ago(g[0].published_at, now),
                "sources_count": n_sources(g),
                "articles": [
                    {
                        "document_id": x.document_id,
                        "source": x.source,
                        "title": x.title,
                        "url": x.url,
                        "ago": ago(x.published_at, now),
                    }
                    for x in g
                ],  # fmt: skip
            }
        )
    return out


@router.get("/status")
def news_status(request: Request, c: Components = Depends(get_components)):
    worker = getattr(request.app.state, "news_worker", None)
    with c.lock:
        total, pending, counts = c.db.article_stats()
    return {
        "enabled": worker is not None,
        "interval_min": c.settings.news_interval_min,
        "total": total,
        "pending_summaries": pending,
        "categories": [
            {"id": cid, "label": cfg.get("label", cid), "count": counts.get(cid, 0)}
            for cid, cfg in {**c.settings.news_topics, **c.settings.news_feeds}.items()
        ],
        **(worker.status if worker else {}),
    }


@router.get("/weather")
def weather(w=Depends(get_weather)):
    """Dự báo thời tiết cho ô thời tiết trên khu Tin tức."""
    if not w._s.weather_enabled:
        raise HTTPException(404, "Chưa bật thời tiết (WEATHER_ENABLED=false)")
    try:
        return w.summary()
    except Exception as e:
        raise HTTPException(502, f"Không lấy được dự báo: {e}") from e


@router.post("/refresh")
def refresh_news(request: Request):
    worker = getattr(request.app.state, "news_worker", None)
    if worker is None:
        raise HTTPException(503, "Chưa bật lấy tin (NEWS_ENABLED=false hoặc API chưa khởi động)")
    worker.request_refresh()
    return {"status": "started"}


def _with_messages(store: ConversationStore, conv) -> dict:
    return {**asdict(conv), "messages": [asdict(m) for m in store.messages(conv.id)]}


@router.get("/conversation")
def current_news_conversation(store: ConversationStore = Depends(get_conversations)):
    """Hội thoại tin tức gần nhất (tạo mới nếu chưa có)."""
    convs = store.list_all(kind="news")
    conv = convs[0] if convs else store.create(NEWS_TITLE, kind="news")
    return _with_messages(store, conv)


@router.post("/conversation")
def new_news_conversation(store: ConversationStore = Depends(get_conversations)):
    return _with_messages(store, store.create(NEWS_TITLE, kind="news"))
