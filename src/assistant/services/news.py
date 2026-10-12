"""Worker tin tức chạy nền: định kỳ lấy bài mới, nạp như tài liệu (nhóm "news"),
tóm tắt từng bài bằng LLM, xoá bài cũ.

Mỗi bài được lưu thành file .md (tiêu đề, nguồn, giờ đăng, link, toàn văn) rồi nạp qua đúng
pipeline của tài liệu, nên tìm kiếm / trích dẫn / "hỏi về bài này" dùng lại toàn bộ cơ chế cũ.
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from starlette.concurrency import run_in_threadpool

from assistant.core.config import Settings
from assistant.news.events import find_event, topic_tags, vec_to_blob
from assistant.news.fetcher import Article, FeedItem, HttpFetcher, extract_article, parse_rss
from assistant.rag.db import ArticleRecord
from assistant.rag.factory import Components
from assistant.rag.grounding import merge_chunks
from assistant.services.llm import LLMClient, LLMError

log = logging.getLogger(__name__)

NEWS_GROUP = "news"
SOURCE_NAMES = {
    "vnexpress.net": "VnExpress",
    "tuoitre.vn": "Tuổi Trẻ",
    "thanhnien.vn": "Thanh Niên",
    "dantri.com.vn": "Dân trí",
    "vietnamnet.vn": "VietnamNet",
}

SUMMARY_SYSTEM = (
    "Tóm tắt bài báo sau trong 2-3 câu tiếng Việt: sự kiện chính, ai, ở đâu, khi nào, số liệu "
    "quan trọng. Khách quan, chỉ dùng thông tin trong bài, không bình luận, không mở đầu bằng "
    "'Bài báo nói về'."
)


def feed_hosts(feeds: dict[str, dict]) -> set[str]:
    """Các tên miền được phép truy cập = tên miền của các RSS đã khai báo."""
    hosts = set()
    for cfg in feeds.values():
        for u in cfg.get("urls", []):
            host = (urlparse(u).hostname or "").removeprefix("www.")
            if host:
                hosts.add(host)
    return hosts


def source_name(url: str) -> str:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    for domain, name in SOURCE_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host


class NewsWorker:
    def __init__(
        self, llm: LLMClient, settings: Settings, components: Components, http=None
    ) -> None:
        self._llm = llm
        self._s = settings
        self._c = components
        self._http = http  # đối tượng có .get(url) -> bytes; mặc định HttpFetcher
        self._trigger = asyncio.Event()
        self.status: dict = {"running": False, "last_fetch": None, "last_result": None}

    # ---- tiện ích ----
    def _locked(self, fn):
        with self._c.lock:
            return fn(self._c.db)

    @property
    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self._s.timezone)

    def _client(self):
        if self._http is None:
            self._http = HttpFetcher(feed_hosts(self._s.news_feeds), self._s.news_request_delay_s)
        return self._http

    def _wait_user_idle(self) -> None:
        """Nhường máy: chờ đến khi người dùng ngừng chat / tải file đủ lâu (chạy trong thread)."""
        while self._c.activity.idle_s() < self._s.news_yield_s:
            time.sleep(0.5)

    def request_refresh(self) -> None:
        """Lấy tin ngay (nút ↻ trên web, make news-fetch)."""
        self._trigger.set()

    # ---- lấy tin ----
    def fetch_once(self) -> dict:
        http = self._client()
        known = self._locked(lambda db: db.article_urls())
        cutoff = datetime.now(self._tz) - timedelta(days=self._s.news_keep_days)
        new, skipped, errors = 0, 0, []
        for cat, cfg in self._s.news_feeds.items():
            for feed_url in cfg.get("urls", []):
                try:
                    items = parse_rss(http.get(feed_url))
                except Exception as e:
                    errors.append(f"RSS {feed_url}: {e}")
                    continue
                for item in items[: self._s.news_max_per_feed]:
                    if item.url in known or item.published < cutoff:
                        continue
                    self._wait_user_idle()
                    known.add(item.url)
                    try:
                        art = extract_article(http.get(item.url), item.title, item.url)
                    except Exception as e:
                        errors.append(f"{item.url}: {e}")
                        continue
                    if self._store(cat, cfg.get("label", cat), item, art, errors):
                        new += 1
                    else:
                        skipped += 1
        removed = self.cleanup()
        result = {"new": new, "skipped": skipped, "removed": removed, "errors": errors[:10]}
        log.info("news fetch: %d bài mới, bỏ %d, xoá %d bài cũ, %d lỗi", new, skipped, removed,
                 len(errors))  # fmt: skip
        return result

    def _store(self, cat: str, label: str, item: FeedItem, art: Article, errors: list) -> bool:
        text = art.text if len(art.text) >= 200 else (art.text or art.lead or item.description)
        if len(text) < 80:  # trang video/ảnh, không có nội dung chữ
            return False
        lead = art.lead or item.description
        published = item.published.astimezone(self._tz)
        source = source_name(item.url)
        root = Path(self._s.news_dir)
        name = f"{published:%Y%m%d}-{hashlib.sha1(item.url.encode()).hexdigest()[:12]}.md"
        path = root / cat / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# {art.title or item.title}\n\n"
            f"Nguồn: {source} · {label} · {published:%H:%M %d/%m/%Y}\nLink: {item.url}\n\n"
            + (f"{lead}\n\n" if lead and lead not in text else "")
            + f"{text}\n",
            encoding="utf-8",
        )

        title = art.title or item.title
        # Phần chậm (embedding trên CPU) chạy NGOÀI khoá để chat / thư viện không phải chờ
        res = self._c.pipeline.ingest_file(
            path, root.parent, department=NEWS_GROUP, lock=self._c.lock
        )
        if res.status == "failed":
            errors.append(f"nạp {item.url}: {res.error}")
            return False
        vec = self._title_vec(title, lead)

        def save(db):
            db.set_digest(res.document_id, "skip")  # bài báo ngắn: luôn đọc nguyên văn
            db.save_article(
                ArticleRecord(
                    document_id=res.document_id,
                    url=item.url,
                    source=source,
                    category=cat,
                    title=title,
                    lead=lead,
                    published_at=published.isoformat(timespec="seconds"),
                    summary=None,
                    summary_status="pending",
                    fetched_at=db.now(),
                    tags=topic_tags(title, lead, self._s.news_topics),
                    event_id=self._event_id(db, res.document_id, vec, published),
                ),
                title_vec=vec,
            )
            return True

        return self._locked(save)

    def _title_vec(self, title: str, lead: str | None) -> bytes:
        """Vector tiêu đề+sapo (embedding: gọi ngoài khoá)."""
        return vec_to_blob(self._c.embedder.embed_passages([f"{title}. {lead or ''}"])[0])

    def _event_id(self, db, doc_id: str, vec: bytes, published: datetime) -> str:
        """Sự kiện mà bài thuộc về: bài giống nhất trong ±48h, không có thì là sự kiện mới."""
        w = timedelta(hours=self._s.news_event_window_h)
        cands = [
            c
            for c in db.article_vectors((published - w).isoformat(), (published + w).isoformat())
            if c[0] != doc_id
        ]
        event_id, _ = find_event(vec, cands, self._s.news_event_threshold)
        return event_id or doc_id

    def backfill_once(self) -> int:
        """Bổ sung nhãn chuyên mục / sự kiện cho các bài lấy từ bản trước. Trả về số bài đã làm."""

        def tags(db):
            missing = db.articles_missing("tags", 200)
            for a in missing:
                db.update_article(
                    a.document_id, tags=topic_tags(a.title, a.lead, self._s.news_topics)
                )
            return len(missing), list(reversed(db.articles_missing("title_vec", 20)))  # cũ trước

        done, todo = self._locked(tags)
        for a in todo:
            vec = self._title_vec(a.title, a.lead)
            published = datetime.fromisoformat(a.published_at)
            self._locked(
                lambda db, a=a, vec=vec, published=published: db.update_article(
                    a.document_id,
                    title_vec=vec,
                    event_id=self._event_id(db, a.document_id, vec, published),
                )
            )
            done += 1
        return done

    def cleanup(self) -> int:
        cutoff = (datetime.now(self._tz) - timedelta(days=self._s.news_keep_days)).isoformat()

        def remove(db):
            ids = db.articles_before(cutoff)
            for doc_id in ids:
                doc = db.get_document_by_id(doc_id)
                self._c.vectors.delete_document(doc_id)
                db.delete_document(doc_id)
                if doc:
                    (Path(self._s.news_dir).parent / doc.source_path).unlink(missing_ok=True)
            return len(ids)

        return self._locked(remove)

    # ---- tóm tắt ----
    async def summarize_once(self) -> bool:
        # LLM chỉ chạy một yêu cầu một lúc: tóm tắt tin chen vào sẽ bắt câu chat phải xếp hàng
        while self._c.activity.idle_s() < self._s.news_yield_s:
            await asyncio.sleep(0.5)
        art = await run_in_threadpool(self._locked, lambda db: db.next_article_to_summarize())
        if art is None:
            return False
        text = await run_in_threadpool(
            self._locked, lambda db: merge_chunks(db.get_document_chunks(art.document_id))
        )
        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": text[:6000]},
        ]
        try:
            parts = [
                t
                async for t in self._llm.stream_chat(
                    messages, temperature=0.1, max_tokens=self._s.news_summary_tokens
                )
            ]
            summary, status = "".join(parts).strip(), "done"
        except LLMError as e:
            log.warning("tóm tắt lỗi %s: %s", art.url, e)
            summary, status = None, "failed"
        await run_in_threadpool(
            self._locked, lambda db: db.set_article_summary(art.document_id, summary, status)
        )
        return True

    # ---- vòng lặp nền ----
    async def run_forever(self) -> None:
        log.info("news worker started (%d chuyên mục)", len(self._s.news_feeds))
        interval = timedelta(minutes=self._s.news_interval_min)
        last: datetime | None = None
        while True:
            now = datetime.now(self._tz)
            if last is None or now - last >= interval or self._trigger.is_set():
                self._trigger.clear()
                self.status["running"] = True
                try:
                    self.status["last_result"] = await run_in_threadpool(self.fetch_once)
                except Exception as e:
                    log.exception("news fetch error")
                    self.status["last_result"] = {"errors": [str(e)]}
                finally:
                    last = datetime.now(self._tz)
                    self.status.update(running=False, last_fetch=last.isoformat(timespec="seconds"))
            try:
                await run_in_threadpool(self._wait_user_idle)
                worked = await run_in_threadpool(self.backfill_once) > 0
                worked = await self.summarize_once() or worked
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("news summarize error")
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(self._trigger.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
