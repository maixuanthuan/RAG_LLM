"""Thử truy xuất:  python scripts/search.py "nhân viên thử việc có được nghỉ phép không" """

import argparse

from assistant.rag.factory import build_components


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("query")
    p.add_argument("--mode", choices=["vector", "keyword", "hybrid"], default="hybrid")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--department", action="append", help="giới hạn phòng ban (có thể lặp lại)")
    args = p.parse_args()

    c = build_components()
    try:
        hits = c.retriever.search(args.query, args.top_k, args.mode, args.department)
        if not hits:
            print("Không tìm thấy gì.")
        for i, h in enumerate(hits, start=1):
            where = f"trang {h.page}" if h.page else "—"
            print(f"\n#{i}  điểm {h.score:.4f}  [{h.department}] {h.document}  ({where})")
            print(f"    mục: {h.section or '—'}   hạng: {h.ranks}")
            snippet = h.text.replace("\n", " ")
            print(f"    {snippet[:300]}{'...' if len(snippet) > 300 else ''}")
    finally:
        c.close()


if __name__ == "__main__":
    main()
