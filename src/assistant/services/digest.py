"""Worker chạy nền: AI đọc trước toàn bộ tài liệu dài, ghi chú từng phần rồi tổng hợp.

Trạng thái nằm trong SQLite (bảng doc_digest) nên tắt API giữa chừng thì lần sau đọc lại;
worker chỉ việc lấy tài liệu 'pending' tiếp theo mỗi vài giây.
"""

import asyncio
import logging

from starlette.concurrency import run_in_threadpool

from assistant.core.config import Settings
from assistant.rag.digest import (
    batch_texts,
    notes_messages,
    page_range,
    reduce_messages,
    split_parts,
)
from assistant.rag.factory import Components
from assistant.rag.grounding import merge_chunks
from assistant.services.llm import LLMClient

log = logging.getLogger(__name__)


class DigestAborted(Exception):
    """Tài liệu bị xoá hoặc nạp lại trong lúc đang đọc."""


async def complete(llm: LLMClient, messages: list[dict], max_tokens: int) -> str:
    parts = [t async for t in llm.stream_chat(messages, temperature=0.1, max_tokens=max_tokens)]
    return "".join(parts).strip()


def labelled(note) -> str:
    where = page_range(note)
    return f"[Phần {note.idx}{' – ' + where if where else ''}]\n{note.text}"


class DigestWorker:
    def __init__(
        self, llm: LLMClient, settings: Settings, components: Components, poll_s: float = 2.0
    ) -> None:
        self._llm = llm
        self._s = settings
        self._c = components
        self._poll = poll_s

    # ---- hàng đợi ----
    def prepare_queue(self) -> None:
        """Lúc khởi động: đọc lại việc dở dang, xếp hàng tài liệu cũ chưa từng được đọc."""
        with self._c.lock:
            db = self._c.db
            db.reset_running_digests()
            for doc_id in db.documents_without_digest():
                chars = len(merge_chunks(db.get_document_chunks(doc_id)))
                db.set_digest(doc_id, "pending" if chars > self._s.focus_max_chars else "skip")

    async def run_forever(self) -> None:
        await run_in_threadpool(self.prepare_queue)
        log.info("digest worker started")
        while True:
            try:
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("digest worker error")
                worked = False
            if not worked:
                await asyncio.sleep(self._poll)

    async def run_once(self) -> bool:
        doc_id = await run_in_threadpool(self._locked, lambda db: db.next_pending_digest())
        if doc_id is None:
            return False
        await self.process(doc_id)
        return True

    # ---- đọc một tài liệu ----
    def _locked(self, fn):
        with self._c.lock:
            return fn(self._c.db)

    async def _save(self, doc_id: str, level: int, idx: int, pf, pt, text: str) -> None:
        def save(db):
            state = db.get_digest(doc_id)
            if state is None or state.status != "running":
                raise DigestAborted(doc_id)
            db.save_note(doc_id, level, idx, pf, pt, text)

        await run_in_threadpool(self._locked, save)

    async def process(self, doc_id: str) -> None:
        doc, chunks = await run_in_threadpool(
            self._locked, lambda db: (db.get_document_by_id(doc_id), db.get_document_chunks(doc_id))
        )
        if doc is None:
            await run_in_threadpool(self._locked, lambda db: db.delete_digest(doc_id))
            return
        parts = split_parts(chunks, self._s.digest_part_chars)
        total = len(parts)
        log.info("digest start: %s (%d phần)", doc.filename, total)
        await run_in_threadpool(self._locked, lambda db: db.set_digest(doc_id, "running", total, 0))
        try:
            notes = []
            for part in parts:
                text = await complete(
                    self._llm,
                    notes_messages(doc.title, part, total),
                    self._s.digest_note_tokens,
                )
                await self._save(doc_id, 1, part.idx, part.page_from, part.page_to, text)
                await run_in_threadpool(
                    self._locked, lambda db, i=part.idx: db.set_digest(doc_id, "running", None, i)
                )
                notes.append(_Note(part.idx, part.page_from, part.page_to, text))
                log.info("digest %s: xong %s", doc.filename, part.label(total))

            # Gộp dần đến khi toàn bộ ghi chú vừa một lần đọc, rồi viết tổng quan
            level = 1
            budget = self._s.focus_max_chars
            while len(notes) > 1 and sum(len(labelled(n)) for n in notes) > budget:
                batches = batch_texts([labelled(n) for n in notes], budget)
                if len(batches) == len(notes):  # từng ghi chú đã quá dài, không gộp được nữa
                    break
                level += 1
                merged = []
                for i, batch in enumerate(batches, start=1):
                    group = [notes[j] for j in batch]
                    text = await complete(
                        self._llm,
                        reduce_messages(doc.title, [labelled(n) for n in group], final=False),
                        self._s.digest_note_tokens,
                    )
                    pages = [p for n in group for p in (n.page_from, n.page_to) if p is not None]
                    note = _Note(i, min(pages, default=None), max(pages, default=None), text)
                    await self._save(doc_id, level, i, note.page_from, note.page_to, text)
                    merged.append(note)
                notes = merged
            overview = await complete(
                self._llm,
                reduce_messages(doc.title, [labelled(n) for n in notes], final=True),
                self._s.digest_note_tokens + 300,
            )
            await self._save(doc_id, 0, 1, None, None, overview)
            await run_in_threadpool(
                self._locked, lambda db: db.set_digest(doc_id, "done", total, total)
            )
            log.info("digest done: %s", doc.filename)
        except DigestAborted:
            log.info("digest aborted (tài liệu bị xoá hoặc nạp lại): %s", doc.filename)
        except Exception as e:
            log.exception("digest failed: %s", doc.filename)
            err = str(e)[:500]

            def mark_failed(db):
                if db.get_digest(doc_id):  # chưa bị xoá
                    db.set_digest(doc_id, "failed", error=err)

            await run_in_threadpool(self._locked, mark_failed)


class _Note:
    """Ghi chú tối giản (idx, khoảng trang, nội dung)."""

    def __init__(self, idx: int, page_from, page_to, text: str) -> None:
        self.idx = idx
        self.page_from = page_from
        self.page_to = page_to
        self.text = text
