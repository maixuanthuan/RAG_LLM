import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import nullcontext
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from starlette.concurrency import run_in_threadpool

from assistant.core.config import Settings
from assistant.news.brief import (
    BRIEF_RULES,
    NEWS_RULES,
    WEATHER_RULES,
    ago,
    brief_category,
    pick_brief,
)
from assistant.news.events import group_events, n_sources, representative
from assistant.news.weather import is_weather_question
from assistant.rag.digest import (
    batch_texts,
    classify_question,
    is_empty_extract,
    page_range,
    reduce_messages,
    scan_messages,
    split_parts,
)
from assistant.rag.embedder import EmbeddingError
from assistant.rag.factory import Components
from assistant.rag.grounding import (
    Source,
    build_system_prompt,
    clean_rewrite,
    merge_chunks,
    now_line,
    pick_documents,
    rewrite_messages,
    unsupported_numbers,
)
from assistant.rag.vectors import VectorStoreError
from assistant.schemas.chat import ChatRequest, ChatResponse, ChatStats, SourceRef
from assistant.services.digest import _Note
from assistant.services.llm import LLMClient, LLMError

log = logging.getLogger(__name__)

Progress = Callable[[str], None]
NEWS_GROUP = "news"  # nhóm tài liệu chứa bài báo (xem services/news.py)


class ChatService:
    """Chat với LLM, có RAG khi kho tài liệu có dữ liệu.

    - Không đính kèm: viết lại câu hỏi nối tiếp -> hybrid retrieval trên toàn thư viện, bỏ
      đoạn dưới ngưỡng độ giống -> trả lời có trích dẫn (hoặc chat thường nếu không có gì).
    - Đính kèm tài liệu (req.source_ids):
      * đủ ngắn: đưa nguyên văn cả tài liệu vào prompt;
      * dài: tuỳ loại câu hỏi -
        overview (tóm tắt, ý chính, đánh giá): ghi chú AI đã đọc trước từ MỌI phần;
        scan (liệt kê tất cả, toàn bộ): đọc lại nguyên văn từng phần với câu hỏi, gom phần trích;
        specific: tìm đoạn liên quan trong tài liệu, kèm đoạn trước/sau và tổng quan.
    """

    def __init__(
        self,
        llm: LLMClient,
        settings: Settings,
        components: Components | None = None,
        weather=None,
    ) -> None:
        self._llm = llm
        self._settings = settings
        self._c = components
        self._weather = weather  # WeatherService (khu Tin tức)

    # ---- truy cập dữ liệu (chạy trong threadpool, giữ khoá) ----
    def _locked(self, fn):
        with self._c.lock:
            return fn(self._c.db)

    def _load_docs(self, ids: list[str]) -> tuple[list, list[str], int]:
        """(các tài liệu tồn tại kèm toàn văn, id không tồn tại, tổng số ký tự)."""

        def load(db):
            docs, missing = [], []
            for doc_id in ids:
                doc = db.get_document_by_id(doc_id)
                if doc is None:
                    missing.append(doc_id)
                else:
                    docs.append((doc, merge_chunks(db.get_document_chunks(doc_id))))
            return docs, missing

        docs, missing = self._locked(load)
        return docs, missing, sum(len(t) for _, t in docs)

    def _search(
        self, query: str, document_ids: list[str] | None, scope: str = "docs"
    ) -> tuple[list[Source], dict]:
        """Tìm đoạn liên quan. Toàn thư viện: bỏ đoạn dưới ngưỡng. Tài liệu đính kèm: lấy
        thêm đoạn kề để đủ ngữ cảnh. scope="docs" không bao giờ tìm trong bài báo,
        scope="news" chỉ tìm trong bài báo."""
        with self._c.lock:
            if self._c.db.count_documents() == 0:
                return [], {"candidates": 0}
            hits = self._c.retriever.search(
                query,
                document_ids=document_ids,
                departments=[NEWS_GROUP] if scope == "news" else None,
                exclude_departments=[NEWS_GROUP] if scope == "docs" else None,
            )
            info: dict = {"candidates": len(hits)}
            if not document_ids:  # người dùng không chỉ định tài liệu -> phải đủ liên quan
                threshold = self._settings.min_relevance
                kept = [
                    h for h in hits if h.vector_score is not None and h.vector_score >= threshold
                ]
                info.update(min_relevance=threshold, dropped=len(hits) - len(kept))
                hits = kept
            k = self._settings.neighbor_chunks if document_ids else 0
            cache: dict[str, list] = {}
            used: set[tuple[str, int]] = set()
            sources: list[Source] = []
            total = 0
            for h in hits:
                if (h.document_id, h.ord) in used:
                    continue
                text, page = h.text, h.page
                if k > 0:
                    if h.document_id not in cache:
                        cache[h.document_id] = self._c.db.get_document_chunks(h.document_id)
                    window = [
                        c
                        for c in cache[h.document_id]
                        if abs(c.ord - h.ord) <= k and (h.document_id, c.ord) not in used
                    ]
                    used.update((h.document_id, c.ord) for c in window)
                    text, page = merge_chunks(window), window[0].page
                if sources and total + len(text) > self._settings.focus_max_chars:
                    break
                total += len(text)
                sources.append(
                    Source(
                        n=len(sources) + 1,
                        document_id=h.document_id,
                        document=h.document,
                        title=h.title,
                        text=text,
                        page=page,
                        section=h.section,
                        score=h.vector_score,
                    )
                )
        return sources, info

    # ---- gọi LLM phụ ----
    async def _complete(self, messages: list[dict], max_tokens: int) -> str:
        parts = [
            t async for t in self._llm.stream_chat(messages, temperature=0.0, max_tokens=max_tokens)
        ]
        return "".join(parts).strip()

    async def _rewrite(self, history: list[dict], question: str) -> str:
        try:
            text = await self._complete(
                rewrite_messages(history, question), self._settings.rewrite_max_tokens
            )
        except LLMError as e:
            log.warning("Viết lại câu hỏi thất bại, dùng câu gốc: %s", e)
            return question
        return clean_rewrite(text, question)

    # ---- tài liệu dài ----
    @staticmethod
    def _src(sources: list, doc, text: str, section: str | None, page=None) -> Source:
        return Source(
            n=len(sources) + 1,
            document_id=doc.id,
            document=doc.filename,
            title=doc.title,
            text=text,
            page=page,
            section=section,
        )

    def _notes_sources(self, docs: list) -> list[Source] | None:
        """Ghi chú AI đã đọc trước (tổng quan + ghi chú mọi phần). None nếu chưa đọc xong."""
        budget = self._settings.focus_max_chars // max(len(docs), 1)

        def load(db):
            out = []
            for doc, text in docs:
                state = db.get_digest(doc.id)
                if state is not None and state.status == "skip":  # tài liệu ngắn: nguyên văn
                    out.append((doc, [], [_Note(1, None, None, text[:budget])]))
                    continue
                if state is None or state.status != "done":
                    return None
                overview = db.get_notes(doc.id, 0)
                chosen = []
                for lv in [lv for lv in db.note_levels(doc.id) if lv >= 1]:
                    chosen = db.get_notes(doc.id, lv)  # mức chi tiết nhất mà vẫn vừa ngân sách
                    if sum(len(n.text) for n in chosen) <= budget:
                        break
                out.append((doc, overview, chosen))
            return out

        loaded = self._locked(load)
        if loaded is None:
            return None
        sources: list[Source] = []
        for doc, overview, notes in loaded:
            for n in overview:
                sources.append(self._src(sources, doc, n.text, "Tổng quan toàn tài liệu"))
            for n in notes:
                label = f"Ghi chú phần {n.idx}" + (f" ({page_range(n)})" if n.page_from else "")
                sources.append(self._src(sources, doc, n.text, label, n.page_from))
        return sources

    async def _scan(self, docs: list, question: str, progress: Progress) -> list[Source]:
        """Đọc lại nguyên văn MỌI phần với câu hỏi, giữ các phần trích có liên quan."""
        extracts: list[tuple] = []  # (doc, part, số phần, phần trích)
        read = 0
        for doc, _ in docs:
            chunks = await run_in_threadpool(
                self._locked, lambda db, d=doc: db.get_document_chunks(d.id)
            )
            parts = split_parts(chunks, self._settings.digest_part_chars)
            for part in parts:
                progress(f"Đang đọc {doc.filename}: {part.label(len(parts))}")
                text = await self._complete(
                    scan_messages(question, doc.title, part, len(parts)),
                    self._settings.scan_extract_tokens,
                )
                read += 1
                if not is_empty_extract(text):
                    extracts.append((doc, part, len(parts), text))
        # Phần trích quá dài so với context -> gộp lại (giữ đủ ý)
        texts = [f"[{p.label(t)}]\n{x}" for _, p, t, x in extracts]
        budget = self._settings.focus_max_chars
        while len(texts) > 1 and sum(map(len, texts)) > budget:
            batches = batch_texts(texts, budget)
            if len(batches) == len(texts):
                break
            progress("Đang tổng hợp các phần trích…")
            texts = [
                await self._complete(
                    reduce_messages(docs[0][0].title, [texts[i] for i in b], final=False),
                    self._settings.digest_note_tokens,
                )
                for b in batches
            ]
            extracts = [(docs[0][0], None, 0, t) for t in texts]
        sources: list[Source] = []
        for doc, part, total, text in extracts:
            label = f"Trích {part.label(total)}" if part else "Tổng hợp phần trích"
            sources.append(self._src(sources, doc, text, label, part.page_from if part else None))
        if not sources:  # đã đọc hết mà không thấy: nói rõ để AI không bịa
            sources.append(
                self._src(
                    sources,
                    docs[0][0],
                    f"Đã đọc toàn bộ {read} phần của tài liệu: không phần nào có thông tin "
                    "liên quan đến câu hỏi.",
                    "Kết quả quét toàn bộ",
                )
            )
        return sources

    async def _wait_digests(self, docs: list, progress: Progress) -> list[str]:
        """Đọc xong toàn bộ rồi mới trả lời: chờ AI đọc trước xong các tài liệu đang được hỏi
        (đưa chúng lên đầu hàng đợi), báo tiến độ cho người dùng trong lúc chờ."""
        if not self._settings.digest_enabled:
            return []
        ids = [d.id for d, _ in docs]
        await run_in_threadpool(self._locked, lambda db: db.prioritize_digests(ids))
        deadline = time.monotonic() + self._settings.digest_wait_timeout_s
        last = None
        while True:
            states = await run_in_threadpool(
                self._locked, lambda db: {i: db.get_digest(i) for i in ids}
            )
            waiting = [
                (d, states[d.id])
                for d, _ in docs
                if states.get(d.id) and states[d.id].status in ("pending", "running")
            ]
            if not waiting:
                break
            if time.monotonic() > deadline:
                return [
                    "Hết thời gian chờ AI đọc tài liệu; câu trả lời chỉ dựa trên phần tìm được."
                ]
            d, st = waiting[0]
            if st.status == "running" and st.parts_total:
                msg = f"AI đang đọc {d.filename}: phần {st.parts_done}/{st.parts_total}"
            else:
                msg = f"Đang chờ đến lượt đọc {d.filename}"
            if len(waiting) > 1:
                msg += f" (còn {len(waiting)} tài liệu)"
            if msg != last:
                progress(msg)
                last = msg
            await asyncio.sleep(self._settings.digest_wait_poll_s)
        failed = [d.filename for d, _ in docs if states[d.id] and states[d.id].status == "failed"]
        if failed:
            return [
                f"Không đọc hết được {', '.join(failed)}; câu trả lời chỉ dựa trên phần tìm được."
            ]
        return []

    async def _long_docs(
        self, docs: list, history: list[dict], question: str, progress: Progress
    ) -> tuple[list[Source], list[str], dict]:
        warnings = await self._wait_digests(docs, progress)
        query = question
        if len(history) > 1:
            query = await self._rewrite(history[:-1], question)
        kind = classify_question(query + " " + question)
        if kind == "overview":
            notes = await run_in_threadpool(self._notes_sources, docs)
            if notes is not None:
                return notes, warnings, {"mode": "overview", "query": query}
            kind = "specific"  # đọc trước lỗi/hết giờ: dùng tìm đoạn liên quan
        if kind == "scan":
            sources = await self._scan(docs, query, progress)
            return sources, warnings, {"mode": "scan", "query": query}
        ids = [doc.id for doc, _ in docs]
        found, info = await run_in_threadpool(self._search, query, ids)
        overviews = await run_in_threadpool(
            self._locked, lambda db: [(d, db.get_notes(d.id, 0)) for d, _ in docs]
        )
        sources: list[Source] = []
        for doc, notes in overviews:
            for n in notes:
                sources.append(self._src(sources, doc, n.text[:2000], "Tổng quan toàn tài liệu"))
        for s in found:
            s.n = len(sources) + 1
            sources.append(s)
        return sources, warnings, {"mode": "search", "query": query, **info}

    # ---- chọn nguồn ----
    async def _find_sources(
        self, req: ChatRequest, history: list[dict], progress: Progress
    ) -> tuple[list[Source], list[str], bool, dict]:
        """Trả về (nguồn, cảnh báo, có tài liệu đính kèm, thông tin debug).

        debug["mode"]: none | full (nguyên văn) | overview (ghi chú mọi phần) | scan (quét
        toàn bộ) | search (tìm đoạn); debug["query"]: câu dùng để tìm (sau khi viết lại).
        """
        if self._c is None or not req.use_documents:
            return [], [], False, {"mode": "none"}
        question = history[-1]["content"]
        warnings: list[str] = []
        if req.scope == "news" and not req.source_ids:
            sources, debug = await self._news_sources(history, question)
            return sources, warnings, False, debug
        if req.source_ids:
            all_docs, missing, _ = await run_in_threadpool(self._load_docs, req.source_ids)
            warnings = [f"Không tìm thấy tài liệu id={m}" for m in missing]
            docs, reason = pick_documents(question, all_docs)
            arts = await run_in_threadpool(
                self._locked, lambda db: db.get_articles([d.id for d, _ in all_docs])
            )

            def name(d):  # bài báo: dùng tiêu đề thay cho tên file nội bộ
                return f"bài báo «{arts[d.id].title}»" if d.id in arts else d.filename

            picked = {
                "attached": [name(d) for d, _ in all_docs],
                "documents": [name(d) for d, _ in docs],
                "pick": reason,
            }
            if docs and sum(len(t) for _, t in docs) <= self._settings.focus_max_chars:
                sources: list[Source] = []
                for doc, text in docs:
                    s = self._src(sources, doc, text, None)
                    s.full = True
                    sources.append(s)
                return sources, warnings, True, {"mode": "full", **picked}
            if docs:
                sources, more, debug = await self._long_docs(docs, history, question, progress)
                return sources, warnings + more, True, {**debug, **picked}
        query = question
        has_docs = await run_in_threadpool(self._locked, lambda db: db.count_documents())
        if len(history) > 1 and has_docs:
            query = await self._rewrite(history[:-1], question)
            log.info("câu hỏi viết lại: %r -> %r", question, query)
        sources, info = await run_in_threadpool(self._search, query, None)
        return sources, warnings, False, {"mode": "search", "query": query, **info}

    # ---- khu Tin tức ----
    def _now(self) -> datetime:
        return datetime.now(ZoneInfo(self._settings.timezone))

    def _brief_sources(self, category: str | None) -> tuple[list[Source], dict]:
        """Bản tin: các bài trong news_digest_hours giờ qua, xoay vòng giữa các chuyên mục."""
        now = self._now()
        since = (now - timedelta(hours=self._settings.news_digest_hours)).isoformat()
        arts = self._locked(lambda db: db.list_articles(since=since, category=category))
        if not arts:  # chưa có bài trong khoảng thời gian: lấy các bài mới nhất đang có
            arts = self._locked(lambda db: db.list_articles(category=category, limit=30))
        cats = {**self._settings.news_feeds, **self._settings.news_topics}
        sources: list[Source] = []
        groups = pick_brief(arts, self._settings.focus_max_chars)
        for g in groups:
            a = representative(g)
            label = cats.get(category or a.category, {}).get("label", a.category)
            n = n_sources(g)
            names = ", ".join(dict.fromkeys(x.source for x in g))
            coverage = f"{n} báo cùng đưa tin ({names}). " if n > 1 else ""
            sources.append(
                Source(
                    n=len(sources) + 1,
                    document_id=a.document_id,
                    document=f"{a.source}: {a.title}",
                    title=a.title,
                    text=f"Chuyên mục: {label}. {coverage}{a.summary or a.lead or ''}",
                    section=f"{label} · {ago(a.published_at, now)}"
                    + (f" · {n} báo đưa tin" if n > 1 else ""),
                    url=a.url,
                )
            )
        return sources, {"articles": len(arts), "events": len(groups)}

    def _decorate_news(self, sources: list[Source]) -> None:
        """Nguồn là bài báo: hiện tên báo + tiêu đề + giờ đăng, kèm link bài gốc."""
        arts = self._locked(lambda db: db.get_articles([s.document_id for s in sources]))
        now = self._now()
        for s in sources:
            a = arts.get(s.document_id)
            if a:
                s.document, s.title, s.url = f"{a.source}: {a.title}", a.title, a.url
                s.section = ago(a.published_at, now)
                s.page = None

    def _weather_sources(self) -> list[Source]:
        """Dự báo thật + các tin thời tiết (bão, mưa lũ...) mới nhất."""
        try:
            text = self._weather.describe()
        except Exception as e:  # mất mạng / API lỗi: nói rõ để AI không đoán
            log.warning("weather error: %s", e)
            text = f"Không lấy được dự báo thời tiết lúc này (lỗi: {e}). Đừng đoán số liệu."
        loc = self._settings.weather_location
        sources = [
            Source(
                n=1,
                document_id="",
                document=f"Dự báo thời tiết {loc} (Open-Meteo)",
                title=f"Dự báo thời tiết {loc}",
                text=text,
                url="https://open-meteo.com/",
            )
        ]
        since = (self._now() - timedelta(hours=48)).isoformat()
        arts = self._locked(lambda db: db.list_articles(since=since, category="thoi-tiet"))
        now = self._now()
        for g in group_events(arts)[:4]:
            a = representative(g)
            sources.append(
                Source(
                    n=len(sources) + 1,
                    document_id=a.document_id,
                    document=f"{a.source}: {a.title}",
                    title=a.title,
                    text=a.summary or a.lead or "",
                    section=ago(a.published_at, now),
                    url=a.url,
                )
            )
        return sources

    async def _news_sources(self, history: list[dict], question: str) -> tuple[list[Source], dict]:
        if self._weather is not None and self._settings.weather_enabled:
            if is_weather_question(question):
                sources = await run_in_threadpool(self._weather_sources)
                return sources, {"mode": "weather"}
        is_brief, category = brief_category(
            question, {**self._settings.news_feeds, **self._settings.news_topics}
        )
        if is_brief:
            sources, info = await run_in_threadpool(self._brief_sources, category)
            debug = {"mode": "news_brief", "category": category, **info}
        else:
            query = question
            if len(history) > 1:
                query = await self._rewrite(history[:-1], question)
            sources, info = await run_in_threadpool(self._search, query, None, "news")
            await run_in_threadpool(self._decorate_news, sources)
            debug = {"mode": "news_search", "query": query, **info}
        if not sources:  # nói rõ để AI không tự bịa tin
            days = self._settings.news_keep_days
            sources = [
                Source(
                    n=1,
                    document_id="",
                    document="Kho tin tức",
                    title="Kho tin tức",
                    text=f"Trong các bài báo đã lấy về ({days:g} ngày gần nhất) không có bài "
                    "nào liên quan đến câu hỏi này.",
                )
            ]
        return sources, debug

    async def _prepare(
        self, req: ChatRequest, progress: Progress
    ) -> tuple[list[dict], list[Source], float, list[str], dict]:
        history = [m.model_dump() for m in req.messages]
        sources, warnings, focus, debug = await self._find_sources(req, history, progress)
        if req.scope == "news" and req.source_ids and sources:
            await run_in_threadpool(self._decorate_news, sources)
        base = f"{self._settings.system_prompt}\n{now_line(self._settings.timezone)}"
        if req.scope == "news":
            base += "\n" + NEWS_RULES
            if debug.get("mode") == "news_brief":
                base += "\n" + BRIEF_RULES
            elif debug.get("mode") == "weather":
                base += "\n" + WEATHER_RULES
        attached = debug.get("attached") or []
        if attached:
            base += (
                f"\nTài liệu đang đính kèm (cũ → mới): {', '.join(attached)}. Tài liệu khác có "
                "nhắc trong lịch sử nhưng không có trong danh sách này là đã bị gỡ: không dùng."
            )
        if len(attached) > 1:
            base += (
                f" Khi người dùng nói 'file này', 'tài liệu này' mà không nêu tên, đó là "
                f"{attached[-1]}."
            )
        if sources:
            system = build_system_prompt(base, sources, focus)
            temperature = self._settings.grounded_temperature
        else:
            system = base
            temperature = self._settings.llm_temperature
        if req.temperature is not None:
            temperature = req.temperature
        messages = [{"role": "system", "content": system}]
        messages += history[-self._settings.history_max_messages :]
        debug["prompt_chars"] = sum(len(m["content"]) for m in messages)
        return messages, sources, temperature, warnings, debug

    # ---- sinh câu trả lời ----
    async def stream(self, req: ChatRequest) -> AsyncIterator[dict]:
        """Phát ra các event: progress, debug, sources, warning, delta, error, done.
        Trong lúc trả lời, việc nền của tin tức tạm dừng để nhường CPU / GPU."""
        with self._c.activity.active() if self._c else nullcontext():
            async for ev in self._stream(req):
                yield ev

    async def _stream(self, req: ChatRequest) -> AsyncIterator[dict]:
        start = time.perf_counter()
        queue: asyncio.Queue[str] = asyncio.Queue()
        task = asyncio.create_task(self._prepare(req, queue.put_nowait))
        getter: asyncio.Future | None = None
        try:
            # Vừa chờ chuẩn bị nguồn xong, vừa chuyển tiếp tin tiến độ (quét tài liệu dài)
            while not task.done():
                getter = getter or asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    yield {"type": "progress", "message": getter.result()}
                    getter = None
            while not queue.empty():
                yield {"type": "progress", "message": queue.get_nowait()}
            messages, sources, temperature, warnings, debug = task.result()
        except (LLMError, EmbeddingError, VectorStoreError) as e:
            log.error("Lỗi tìm nguồn: %s", e)
            yield {"type": "error", "message": str(e)}
            stats = ChatStats(
                total_s=0, chunks=0, time_to_first_token_s=None, approx_tokens_per_s=None
            )
            yield {"type": "done", "stats": stats.model_dump()}
            return
        finally:
            if getter is not None:
                getter.cancel()
            if not task.done():  # client ngắt kết nối giữa chừng
                task.cancel()
        retrieval_s = time.perf_counter() - start
        yield {"type": "debug", **debug}
        if sources:
            yield {"type": "sources", "sources": [s.ref() for s in sources]}
        for w in warnings:
            yield {"type": "warning", "message": w}

        first_token_s: float | None = None
        chunks = 0
        parts: list[str] = []
        try:
            async for text in self._llm.stream_chat(
                messages,
                temperature=temperature,
                max_tokens=req.max_tokens or self._settings.llm_max_tokens,
            ):
                if first_token_s is None:
                    first_token_s = time.perf_counter() - start
                chunks += 1
                parts.append(text)
                yield {"type": "delta", "text": text}
        except LLMError as e:
            log.error("LLM error: %s", e)
            yield {"type": "error", "message": str(e)}

        max_tokens = req.max_tokens or self._settings.llm_max_tokens
        if chunks >= max_tokens - 1:  # Ollama/vLLM stream ~1 token mỗi chunk
            yield {
                "type": "warning",
                "message": f"Câu trả lời có thể bị cắt do chạm giới hạn {max_tokens} token "
                "(LLM_MAX_TOKENS). Hỏi 'tiếp tục' hoặc tăng giới hạn trong .env.",
            }
        if sources and parts:
            evidence = "\n".join(f"{s.label()}\n{s.text}" for s in sources)
            bad = unsupported_numbers("".join(parts), evidence + "\n" + req.messages[-1].content)
            if len(bad) >= 5:
                yield {
                    "type": "warning",
                    "message": f"CẢNH BÁO: {len(bad)} số liệu không có trong tài liệu, câu trả lời "
                    "có thể đã bị AI bịa. Đừng tin các con số: " + ", ".join(bad),
                }
            elif bad:
                yield {
                    "type": "warning",
                    "message": "Các số sau không thấy nguyên văn trong nguồn, nên kiểm tra lại: "
                    + ", ".join(bad),
                }

        total_s = time.perf_counter() - start
        gen_s = total_s - (first_token_s or 0.0)
        stats = ChatStats(
            retrieval_s=round(retrieval_s, 3),
            time_to_first_token_s=round(first_token_s, 3) if first_token_s is not None else None,
            total_s=round(total_s, 3),
            chunks=chunks,
            approx_tokens_per_s=round(chunks / gen_s, 1) if chunks and gen_s > 0 else None,
        )
        log.info(
            "chat q=%r mode=%s query=%r sources=[%s] retrieval=%.2fs ttft=%ss total=%.2fs "
            "tok/s=%s answer_chars=%d",
            req.messages[-1].content[:150],
            debug.get("mode"),
            debug.get("query"),
            ", ".join(
                s.document + (f"@{s.score:.2f}" if s.score is not None else "") for s in sources
            ),
            stats.retrieval_s,
            stats.time_to_first_token_s,
            stats.total_s,
            stats.approx_tokens_per_s,
            sum(len(p) for p in parts),
        )
        yield {"type": "done", "stats": stats.model_dump()}

    async def complete(self, req: ChatRequest) -> ChatResponse:
        parts: list[str] = []
        stats: dict = {}
        sources: list[SourceRef] = []
        warnings: list[str] = []
        async for ev in self.stream(req):
            if ev["type"] == "delta":
                parts.append(ev["text"])
            elif ev["type"] == "sources":
                sources = [SourceRef(**s) for s in ev["sources"]]
            elif ev["type"] == "warning":
                warnings.append(ev["message"])
            elif ev["type"] == "error":
                raise LLMError(ev["message"])
            elif ev["type"] == "done":
                stats = ev["stats"]
        return ChatResponse(
            answer="".join(parts),
            model=self._llm.model,
            stats=ChatStats(**stats),
            sources=sources,
            warnings=warnings,
        )
