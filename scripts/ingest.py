"""Nạp tài liệu vào hệ thống.

python scripts/ingest.py data/sample_docs            # cả thư mục (thư mục con = phòng ban)
python scripts/ingest.py file.pdf --department HR    # một file
python scripts/ingest.py data/docs --force           # nạp lại kể cả file không đổi
python scripts/ingest.py --reset data/docs           # xoá sạch dữ liệu rồi nạp lại
python scripts/ingest.py --stats                     # chỉ xem thống kê
"""

import argparse
import time
from pathlib import Path

from assistant.rag.factory import build_components


def print_stats(c) -> None:
    s = c.db.stats()
    print(f"Tài liệu: {s['documents']}, chunk: {s['chunks']}, vector: {c.vectors.count()}")
    print(f"Embedding: {s['embed_model']} ({s['embed_dim']} chiều)")
    for row in s["by_department"]:
        print(f"  {row['department']:<12} {row['docs']} tài liệu, {row['chunks']} chunk")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("path", nargs="?")
    p.add_argument("--department", help="ghi đè phòng ban (mặc định: tên thư mục con)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--reset", action="store_true", help="xoá toàn bộ dữ liệu trước khi nạp")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    c = build_components()
    try:
        if args.reset:
            c.vectors.reset()
            c.db.reset()
            print("Đã xoá toàn bộ dữ liệu cũ.")
        if args.stats or not args.path:
            print_stats(c)
            return
        start = time.perf_counter()
        results = c.pipeline.ingest_path(Path(args.path), args.department, args.force)
        counts: dict[str, int] = {}
        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
            extra = f"{r.chunks} chunk" + (f", {r.pages} trang" if r.pages else "")
            print(f"[{r.status.upper():<8}] {r.path}  ({extra})")
            for w in r.warnings:
                print(f"           cảnh báo: {w}")
            if r.error:
                print(f"           LỖI: {r.error}")
        print(f"\nXong trong {time.perf_counter() - start:.1f}s: {counts}")
        print_stats(c)
    finally:
        c.close()


if __name__ == "__main__":
    main()
