"""Đánh giá chất lượng truy xuất trên bộ câu hỏi có đáp án (golden set).

    python scripts/eval_retrieval.py eval/golden_sample.jsonl

Mỗi dòng JSONL: {"question": "...", "documents": ["file.docx"], "contains": ["cụm bằng chứng"]}
Một đoạn tìm được tính là ĐÚNG nếu thuộc một trong các file `documents` và (nếu có `contains`)
chứa ít nhất một cụm trong `contains` (so khớp không phân biệt dấu/hoa thường).
"""

import argparse
import json
import statistics
import time

from assistant.ingestion.text import fold
from assistant.rag.factory import build_components

MODES = ["vector", "keyword", "hybrid"]
KS = (1, 3, 5, 10)


def relevant(hit, item: dict) -> bool:
    if hit.document.lower() not in {d.lower() for d in item["documents"]}:
        return False
    needles = item.get("contains") or []
    return not needles or any(fold(n) in fold(hit.text) for n in needles)


def first_rank(hits, item: dict) -> int | None:
    for i, h in enumerate(hits, start=1):
        if relevant(h, item):
            return i
    return None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("golden")
    p.add_argument("--department", action="append")
    args = p.parse_args()

    with open(args.golden, encoding="utf-8") as f:
        items = [json.loads(line) for line in f if line.strip()]

    c = build_components()
    try:
        results: dict[str, list[int | None]] = {}
        latency: dict[str, list[float]] = {}
        details: dict[str, list] = {}
        for mode in MODES:
            results[mode], latency[mode], details[mode] = [], [], []
            for item in items:
                t0 = time.perf_counter()
                hits = c.retriever.search(item["question"], max(KS), mode, args.department)
                latency[mode].append(time.perf_counter() - t0)
                results[mode].append(first_rank(hits, item))
                details[mode].append(hits)

        n = len(items)
        print(f"{n} câu hỏi\n")
        header = "chế độ   " + "".join(f"hit@{k:<4}" for k in KS) + "MRR@10   TB(ms)"
        print(header)
        print("-" * len(header))
        for mode in MODES:
            ranks = results[mode]
            hit_rates = [sum(1 for r in ranks if r and r <= k) / n for k in KS]
            mrr = sum(1 / r for r in ranks if r) / n
            ms = statistics.mean(latency[mode]) * 1000
            print(f"{mode:<9}" + "".join(f"{h:<8.2f}" for h in hit_rates) + f"{mrr:<9.3f}{ms:.0f}")

        print("\nCâu hỏi hybrid chưa đưa đáp án vào top 3:")
        bad = 0
        for item, rank, hits in zip(items, results["hybrid"], details["hybrid"], strict=True):
            if rank is None or rank > 3:
                bad += 1
                print(f"\n- {item['question']}")
                print(f"  đáp án nằm ở: {item['documents']} chứa {item.get('contains')}")
                print(f"  hạng tìm được: {rank}")
                for h in hits[:3]:
                    print(f"    · {h.document} | {h.section} | {h.text[:80]!r}")
        if bad == 0:
            print("  (không có)")
    finally:
        c.close()


if __name__ == "__main__":
    main()
