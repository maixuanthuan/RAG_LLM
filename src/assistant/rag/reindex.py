"""Dựng lại kho vector từ nội dung đã lưu trong SQLite (không cần file gốc).

Tự chạy lúc API khởi động khi cần:
- đổi model embedding (vd máy dev dùng `bge-m3-cpu`, server dùng `bge-m3` trên GPU);
- chép thư mục data/ sang máy mới mà kho vector (Qdrant) ở đó còn trống / thiếu.

Chạy tay (ép dựng lại):  python -m assistant.rag.reindex --force
"""

import argparse
import logging
import time

from assistant.rag.factory import Components

log = logging.getLogger(__name__)


def needs_reindex(c: Components) -> str | None:
    """Lý do phải dựng lại kho vector, hoặc None nếu kho đang khớp với dữ liệu."""
    with c.lock:
        n_chunks = c.db.count_chunks()
        if not n_chunks:
            return None
        stored = c.db.get_meta("embed_model")
        if stored != c.embedder.model:
            return f"đổi model embedding {stored} -> {c.embedder.model}"
        n_vec = c.vectors.count()
    if n_vec < n_chunks:
        return f"kho vector thiếu ({n_vec}/{n_chunks} đoạn)"
    return None


def reindex(c: Components, batch: int = 64) -> int:
    """Embedding lại mọi đoạn bằng model hiện tại, ghi đè kho vector. Trả về số đoạn."""
    with c.lock:
        docs = c.db.list_documents()
        c.vectors.reset()
    total = sum(d.n_chunks for d in docs)
    done, t0, ready = 0, time.perf_counter(), False
    for doc in docs:
        with c.lock:
            chunks = c.db.get_document_chunks(doc.id)
        for i in range(0, len(chunks), batch):
            part = chunks[i : i + batch]
            # cùng dạng văn bản với lúc nạp (ingestion/pipeline.py)
            vecs = c.embedder.embed_passages(
                [f"{x.title}{f' - {x.section}' if x.section else ''}\n{x.text}" for x in part]
            )
            with c.lock:
                if not ready:
                    c.vectors.ensure_collection(len(vecs[0]))
                    c.db.set_meta("embed_model", c.embedder.model)
                    c.db.set_meta("embed_dim", str(len(vecs[0])))
                    ready = True
                c.vectors.upsert(
                    [x.id for x in part],
                    vecs,
                    [
                        {
                            "document_id": x.document_id,
                            "document": x.document,
                            "department": x.department,
                            "page": x.page,
                            "section": x.section,
                            "ord": x.ord,
                        }
                        for x in part
                    ],
                )
            done += len(part)
            log.info("dựng lại kho vector: %d/%d đoạn (%.0fs)", done, total,
                     time.perf_counter() - t0)  # fmt: skip
    return done


def ensure_index(c: Components) -> None:
    """Gọi lúc khởi động: dựng lại kho vector nếu cần. Lỗi thì ghi log, không chặn API."""
    try:
        reason = needs_reindex(c)
        if reason:
            log.warning("Dựng lại kho vector: %s", reason)
            log.info("Xong: %d đoạn", reindex(c))
    except Exception:
        log.exception("Không dựng lại được kho vector (tìm kiếm có thể thiếu kết quả)")


def main() -> None:
    from assistant.core.logging import setup_logging
    from assistant.rag.factory import build_components

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true", help="dựng lại dù kho đang khớp")
    args = p.parse_args()
    setup_logging("INFO")
    c = build_components()
    try:
        reason = "--force" if args.force else needs_reindex(c)
        if not reason:
            print("Kho vector đang khớp dữ liệu, không cần dựng lại.")
            return
        print(f"Dựng lại kho vector ({reason})...")
        print(f"Xong: {reindex(c)} đoạn")
    finally:
        c.close()


if __name__ == "__main__":
    main()
