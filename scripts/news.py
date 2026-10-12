"""Công cụ tin tức trên terminal.

python scripts/news.py check     kiểm tra từng nguồn RSS + thử lấy toàn văn 1 bài (không ghi gì)
python scripts/news.py fetch     bảo API đang chạy lấy tin ngay, chờ xong rồi in kết quả
"""

import argparse
import sys
import time

import httpx

from assistant.core.config import get_settings
from assistant.news.fetcher import HttpFetcher, extract_article, parse_rss
from assistant.services.news import feed_hosts, source_name


def check(url: str | None = None) -> int:
    s = get_settings()
    feeds = {"thu": {"label": "Nguồn thử", "urls": [url]}} if url else s.news_feeds
    http = HttpFetcher(feed_hosts(feeds), s.news_request_delay_s)
    ok, sample = 0, None
    samples = []
    try:
        for cat, cfg in feeds.items():
            for url in cfg.get("urls", []):
                try:
                    items = parse_rss(http.get(url))
                except Exception as e:
                    print(f"  ✗ {cfg.get('label', cat):<12} {url}\n      LỖI: {e}")
                    continue
                ok += 1
                newest = items[0] if items else None
                when = f"{newest.published:%H:%M %d/%m}" if newest else "-"
                print(f"  ✓ {cfg.get('label', cat):<12} {len(items):>3} bài, mới nhất {when}: "
                      f"{newest.title[:60] if newest else ''}")  # fmt: skip
                sample = sample or newest
                if newest and source_name(newest.url) not in {source_name(x.url) for x in samples}:
                    samples.append(newest)  # thử toàn văn 1 bài của mỗi báo
        for item in samples:
            print(f"\nThử lấy toàn văn ({source_name(item.url)}): {item.url}")
            try:
                art = extract_article(http.get(item.url), item.title, item.url)
            except Exception as e:
                print(f"  LỖI: {e}")
                continue
            print(f"  Tiêu đề : {art.title}")
            print(f"  Sapo    : {art.lead[:200]}")
            print(f"  Nội dung: {len(art.text)} ký tự")
            print("  " + art.text[:300].replace("\n", " ") + "…")
            if len(art.text) < 200:
                print("  CẢNH BÁO: nội dung quá ngắn, bộ đọc chưa lấy được bài của báo này.")
    finally:
        http.close()
    print(f"\n{ok} nguồn RSS hoạt động.")
    if not url and s.weather_enabled:
        from assistant.news.weather import WeatherService

        try:
            w = WeatherService(s).summary()
            c = w["current"]
            print(f"Thời tiết {w['location']}: {c['icon']} {c['temp']}°C, {c['desc']}, "
                  f"độ ẩm {c['humidity']}% (Open-Meteo hoạt động)")  # fmt: skip
        except Exception as e:
            print(f"Thời tiết: LỖI {e}")
    return 0 if ok else 1


def fetch(api: str) -> int:
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{api}/api/news/refresh")
        if r.status_code != 200:
            print(f"LỖI: {r.status_code} {r.text}\n(API phải đang chạy: make dev-up)")
            return 1
        print("Đang lấy tin", end="", flush=True)
        time.sleep(2)
        for _ in range(600):
            st = c.get(f"{api}/api/news/status").json()
            if not st.get("running"):
                break
            print(".", end="", flush=True)
            time.sleep(2)
        res = st.get("last_result") or {}
        print(f"\nXong: {res.get('new', 0)} bài mới, tổng {st['total']} bài, "
              f"{st['pending_summaries']} bài đang chờ AI tóm tắt.")  # fmt: skip
        for e in res.get("errors", []):
            print("  lỗi:", e)
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["check", "fetch"])
    p.add_argument("--api", default="http://localhost:8080")
    p.add_argument("--url", help="(check) thử một link RSS bất kỳ")
    args = p.parse_args()
    sys.exit(check(args.url) if args.cmd == "check" else fetch(args.api))


if __name__ == "__main__":
    main()
