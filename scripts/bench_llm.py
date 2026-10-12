"""Benchmark LLM qua chính API của project: TTFT, tốc độ sinh, tải đồng thời.

Ví dụ:
    python scripts/bench_llm.py --concurrency 1 --rounds 3
    python scripts/bench_llm.py --concurrency 8 --rounds 2
Chạy ở máy dev rồi chạy lại trên server để so sánh (ghi vào docs/benchmarks/).
"""

import argparse
import asyncio
import json
import statistics
import time

import httpx

PROMPTS = [
    "Giải thích ngắn gọn RAG là gì và vì sao doanh nghiệp cần nó.",
    "Viết email ngắn thông báo lịch bảo trì hệ thống vào cuối tuần cho toàn công ty.",
    "Tóm tắt sự khác nhau giữa thử việc và hợp đồng chính thức theo cách dễ hiểu.",
    "Liệt kê 5 lưu ý khi đặt tên file tài liệu nội bộ để dễ tìm kiếm.",
    "Giải thích cách hoạt động của reranker trong hệ thống tìm kiếm tài liệu.",
]


async def one_request(http: httpx.AsyncClient, api: str, prompt: str) -> dict:
    start = time.perf_counter()
    ttft = None
    chunks = 0
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": 256}
    async with http.stream("POST", f"{api}/api/chat", json=body) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev["type"] == "delta":
                if ttft is None:
                    ttft = time.perf_counter() - start
                chunks += 1
            elif ev["type"] == "error":
                raise RuntimeError(ev["message"])
    total = time.perf_counter() - start
    gen = total - (ttft or 0)
    return {
        "ttft": ttft or total,
        "total": total,
        "chunks": chunks,
        "tps": chunks / gen if gen else 0,
    }


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://localhost:8080")
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--rounds", type=int, default=3)
    args = p.parse_args()

    results: list[dict] = []
    wall_start = time.perf_counter()
    async with httpx.AsyncClient(timeout=None) as http:
        for r in range(args.rounds):
            batch = [
                PROMPTS[(r * args.concurrency + i) % len(PROMPTS)] for i in range(args.concurrency)
            ]
            results += await asyncio.gather(*(one_request(http, args.api, q) for q in batch))
    wall = time.perf_counter() - wall_start

    ttfts = [x["ttft"] for x in results]
    totals = sorted(x["total"] for x in results)
    total_chunks = sum(x["chunks"] for x in results)
    print(f"Số request            : {len(results)} (đồng thời {args.concurrency})")
    print(f"TTFT trung bình       : {statistics.mean(ttfts):.2f} s")
    print(f"Tổng thời gian p50/max: {statistics.median(totals):.2f} / {totals[-1]:.2f} s")
    print(f"Tốc độ mỗi request    : {statistics.mean(x['tps'] for x in results):.1f} chunk/s")
    print(f"Thông lượng tổng      : {total_chunks / wall:.1f} chunk/s")


if __name__ == "__main__":
    asyncio.run(main())
