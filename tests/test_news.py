"""Tin tức: đọc RSS, lấy toàn văn, worker nạp/tóm tắt/dọn tin. HTTP giả lập, không cần mạng."""

import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from assistant.news.fetcher import HttpFetcher, extract_article, parse_rss
from assistant.services.news import NewsWorker, source_name
from tests.conftest import FakeLLM


def rss(items) -> bytes:
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link><pubDate>{format_datetime(d)}</pubDate>"
        f"<description><![CDATA[<a href='x'><img src='y'/></a>{desc}]]></description></item>"
        for t, u, d, desc in items
    )
    return f'<?xml version="1.0"?><rss><channel>{body}</channel></rss>'.encode()


def page(title, lead, paras) -> bytes:
    ps = "".join(f'<p class="Normal">{p}</p>' for p in paras)
    return (
        f"<html><body><nav><p>Menu trang chủ thời sự kinh doanh thế giới đời sống</p></nav>"
        f'<h1 class="title-detail">{title}</h1><p class="description">{lead}</p>'
        f'<article class="fck_detail">{ps}<script>ads()</script></article></body></html>'
    ).encode()


NOW = datetime.now(timezone.utc)
GOLD = "https://vnexpress.net/gia-vang-tang-1.html"
ROAD = "https://vnexpress.net/cao-toc-thong-xe-2.html"
BODY = "Giá vàng miếng SJC tăng 2 triệu đồng mỗi lượng, lên 120 triệu đồng sáng nay. " * 4


class FakeHttp:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if url not in self.pages:
            raise RuntimeError("404")
        return self.pages[url]


@pytest.fixture
def worker(components, tmp_path):
    s = components.settings
    s.news_dir = str(tmp_path / "news")
    s.news_feeds = {
        "kinh-doanh": {"label": "Kinh doanh", "urls": ["https://vnexpress.net/rss/kinh-doanh.rss"]},
        "giao-thong": {"label": "Giao thông", "urls": ["https://vnexpress.net/rss/oto-xe-may.rss"]},
    }
    http = FakeHttp(
        {
            "https://vnexpress.net/rss/kinh-doanh.rss": rss(
                [
                    ("Giá vàng tăng mạnh", GOLD, NOW - timedelta(hours=2), "Vàng tăng"),
                    ("Tin rất cũ", "https://vnexpress.net/cu.html", NOW - timedelta(days=30), ""),
                ]
            ),
            "https://vnexpress.net/rss/oto-xe-may.rss": rss(
                [("Cao tốc thông xe", ROAD, NOW - timedelta(hours=5), "Thông xe")]
            ),
            GOLD: page("Giá vàng tăng mạnh &amp; lập đỉnh", "Sapo giá vàng.", [BODY]),
            ROAD: page("Cao tốc thông xe", "Sapo cao tốc.", ["Cao tốc Bắc Nam đoạn X dài 50 km "
                                                             "chính thức thông xe sáng nay. " * 4]),
        }
    )  # fmt: skip
    return NewsWorker(FakeLLM(tokens=["Giá vàng SJC tăng 2 triệu đồng."]), s, components, http)


def test_parse_rss_sorts_dedupes_and_cleans():
    data = rss(
        [
            ("Cũ hơn", "https://a.vn/1", NOW - timedelta(hours=3), "x"),
            ("Mới &amp; nóng", "https://a.vn/2", NOW, "<b>Mô tả</b> &nbsp;ngắn"),
            ("Trùng", "https://a.vn/2", NOW, "y"),
        ]
    )
    items = parse_rss(data)
    assert [i.url for i in items] == ["https://a.vn/2", "https://a.vn/1"]
    assert items[0].title == "Mới & nóng" and items[0].description == "Mô tả ngắn"


def test_parse_rss_rejects_entity_bomb():
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss>&a;</rss>'
    from defusedxml import EntitiesForbidden

    with pytest.raises(EntitiesForbidden):
        parse_rss(bomb)


def test_extract_article_vnexpress_layout():
    art = extract_article(page("Tiêu đề", "Đoạn sapo.", ["Đoạn một đủ dài " * 5, "Đoạn hai."]))
    assert art.title == "Tiêu đề" and art.lead == "Đoạn sapo."
    assert "Đoạn một" in art.text and "Đoạn hai." in art.text  # p.Normal giữ cả đoạn ngắn
    assert "Menu" not in art.text and "ads()" not in art.text


def test_http_fetcher_only_allows_listed_domains():
    f = HttpFetcher({"vnexpress.net"}, delay_s=0)
    assert f.allowed_host("https://vnexpress.net/a.html")
    assert f.allowed_host("https://e.vnexpress.net/a.html")
    assert not f.allowed_host("https://vnexpress.net.evil.com/a.html")
    assert not f.allowed_host("file:///etc/passwd")
    with pytest.raises(PermissionError):
        f.get("http://169.254.169.254/latest")
    f.close()


def test_source_name():
    assert source_name("https://vnexpress.net/x") == "VnExpress"
    assert source_name("https://www.tuoitre.vn/x") == "Tuổi Trẻ"


def test_fetch_ingests_new_articles_as_news_documents(worker, components):
    result = worker.fetch_once()
    assert result["new"] == 2 and not result["errors"]
    arts = {a.url: a for a in components.db.list_articles()}
    assert set(arts) == {GOLD, ROAD}  # bài quá cũ (30 ngày) bị bỏ qua
    gold = arts[GOLD]
    assert gold.title == "Giá vàng tăng mạnh & lập đỉnh" and gold.source == "VnExpress"
    assert gold.category == "kinh-doanh" and gold.summary_status == "pending"
    doc = components.db.get_document_by_id(gold.document_id)
    assert doc.department == "news"
    assert components.db.get_digest(gold.document_id).status == "skip"
    # lần sau không tải lại bài đã có
    calls = len(worker._http.calls)
    assert worker.fetch_once()["new"] == 0
    assert GOLD not in worker._http.calls[calls:]


def test_summarize_and_cleanup(worker, components):
    import asyncio

    worker.fetch_once()
    while asyncio.run(worker.summarize_once()):
        pass
    arts = components.db.list_articles()
    assert all(a.summary_status == "done" and a.summary for a in arts)
    components.settings.news_keep_days = 0.1  # 2.4 giờ: bài 5 giờ trước bị xoá
    assert worker.cleanup() == 1
    assert [a.url for a in components.db.list_articles()] == [GOLD]
    assert components.vectors.count() == components.db.stats()["chunks"]


def test_broken_feed_reported_not_fatal(worker):
    worker._s.news_feeds["hong"] = {"label": "Hỏng", "urls": ["https://vnexpress.net/rss/x.rss"]}
    result = worker.fetch_once()
    assert result["new"] == 2 and any("x.rss" in e for e in result["errors"])


# ---- hai khu tách biệt: chat tài liệu / tin tức ----
import json  # noqa: E402


def sse(r):
    return [json.loads(x[6:]) for x in r.text.splitlines() if x.startswith("data: ")]


def of_type(events, kind):
    return [e for e in events if e["type"] == kind]


def ask(client, cid, q):
    return sse(client.post(f"/api/conversations/{cid}/messages", json={"content": q}))


def test_docs_chat_never_uses_news(worker, make_client):
    worker.fetch_once()
    fake = FakeLLM()
    client = make_client(fake)
    client.post(
        "/api/documents", files={"file": ("Quy_che.txt", "1. Phép\nNghỉ 12 ngày.\n".encode())}
    )
    cid = client.post("/api/conversations").json()["id"]
    events = ask(client, cid, "giá vàng SJC hôm nay bao nhiêu")
    assert "SJC" not in fake.last_messages[0]["content"]
    assert all("vnexpress" not in (s.get("url") or "") for e in of_type(events, "sources")
               for s in e["sources"])  # fmt: skip
    # thư viện tài liệu không hiện bài báo, danh sách chat không hiện hội thoại tin tức
    assert [d["filename"] for d in client.get("/api/documents").json()] == ["Quy_che.txt"]
    client.get("/api/news/conversation")
    assert [c["kind"] for c in client.get("/api/conversations").json()] == ["chat"]


def test_news_brief_groups_latest_articles_with_links(worker, make_client):
    worker.fetch_once()
    fake = FakeLLM()
    client = make_client(fake)
    conv = client.get("/api/news/conversation").json()
    assert conv["kind"] == "news"
    events = ask(client, conv["id"], "Chào buổi sáng, hôm nay có tin gì?")
    assert of_type(events, "debug")[0]["mode"] == "news_brief"
    sources = of_type(events, "sources")[0]["sources"]
    assert {s["url"] for s in sources} == {GOLD, ROAD}
    assert all(s["document"].startswith("VnExpress: ") for s in sources)
    system = fake.last_messages[0]["content"]
    assert "BẢN TIN" in system and "khu Tin tức" in system


def test_news_brief_by_category(worker, make_client):
    worker.fetch_once()
    client = make_client(FakeLLM())
    cid = client.get("/api/news/conversation").json()["id"]
    events = ask(client, cid, "tin giao thông hôm nay")
    sources = of_type(events, "sources")[0]["sources"]
    assert [s["url"] for s in sources] == [ROAD]


def test_news_specific_question_searches_articles(worker, make_client):
    worker.fetch_once()
    fake = FakeLLM()
    client = make_client(fake)
    cid = client.get("/api/news/conversation").json()["id"]
    events = ask(client, cid, "giá vàng SJC tăng bao nhiêu")
    assert of_type(events, "debug")[0]["mode"] == "news_search"
    sources = of_type(events, "sources")[0]["sources"]
    assert sources[0]["url"] == GOLD and "SJC" in fake.last_messages[0]["content"]


def test_news_without_articles_says_so(make_client):
    fake = FakeLLM()
    client = make_client(fake)
    cid = client.get("/api/news/conversation").json()["id"]
    ask(client, cid, "hôm nay có tin gì?")
    assert "không có bài nào" in fake.last_messages[0]["content"]


def test_ask_about_one_article(worker, make_client, components):
    worker.fetch_once()
    fake = FakeLLM()
    client = make_client(fake)
    gold = [a for a in components.db.list_articles() if a.url == GOLD][0]
    cid = client.post("/api/news/conversation").json()["id"]
    client.post(f"/api/conversations/{cid}/attachments", json={"document_id": gold.document_id})
    events = ask(client, cid, "bài này nói gì?")
    src = of_type(events, "sources")[0]["sources"][0]
    assert src["full"] and src["url"] == GOLD and src["document"].startswith("VnExpress: ")
    assert "120 triệu" in fake.last_messages[0]["content"]


def test_news_list_and_status_api(worker, make_client):
    worker.fetch_once()
    client = make_client(FakeLLM())
    items = client.get("/api/news").json()
    assert [i["url"] for i in items] == [GOLD, ROAD]  # mới nhất trước
    assert items[0]["category_label"] == "Kinh doanh" and items[0]["ago"].endswith("trước")
    assert [i["url"] for i in client.get("/api/news?category=giao-thong").json()] == [ROAD]
    st = client.get("/api/news/status").json()
    assert st["total"] == 2 and st["pending_summaries"] == 2 and not st["enabled"]
    assert client.post("/api/news/refresh").status_code == 503  # test không chạy worker nền


def test_extract_generic_site_with_trafilatura():
    para = "Sáng nay, UBND TP Hà Nội tổ chức họp báo công bố kế hoạch mở rộng tuyến đường {}. "
    html_doc = f"""<html><head><title>Hà Nội mở rộng đường - Báo X</title>
      <meta name="description" content="Hà Nội công bố kế hoạch mở rộng 3 tuyến đường lớn."></head>
      <body><header><nav><a href="/">Trang chủ</a> <a href="/thoi-su">Thời sự</a></nav></header>
      <main><article><h1>Hà Nội mở rộng 3 tuyến đường lớn</h1>
      {"".join(f"<p>{para.format(i) * 2}</p>" for i in range(4))}
      </article>
      <aside class="related"><h3>Tin liên quan</h3><ul><li><a href="/x">Tin khác không liên quan
      tới bài này nhưng có tiêu đề rất dài để thử bộ lọc</a></li></ul></aside></main>
      <footer>Bản quyền thuộc về Báo X. Liên hệ quảng cáo.</footer></body></html>"""
    art = extract_article(html_doc, "dự phòng", "https://tuoitre.vn/ha-noi-mo-rong.htm")
    assert "mở rộng tuyến đường 3" in art.text and len(art.text) >= 200
    assert "Bản quyền" not in art.text and "Trang chủ" not in art.text
    assert art.lead == "Hà Nội công bố kế hoạch mở rộng 3 tuyến đường lớn."


# ---- chuyên mục theo từ khoá + gom sự kiện ----
from assistant.news.events import find_event, topic_tags, vec_to_blob  # noqa: E402

TOPICS = {
    "thoi-tiet": {"label": "Thời tiết", "keywords": ["bão", "mưa lớn", "nắng nóng"]},
    "chinh-tri": {"label": "Chính trị", "keywords": ["Quốc hội", "Thủ tướng"]},
}


def test_topic_tags_match_with_diacritics_and_whole_words():
    assert topic_tags("Bão số 12 đổ bộ", "", TOPICS) == ",thoi-tiet,"
    assert topic_tags("Báo chí đưa tin", "Bảo hiểm tăng", TOPICS) == ","  # báo/bảo != bão
    assert topic_tags("Thủ tướng chỉ đạo ứng phó bão", None, TOPICS) == ",thoi-tiet,chinh-tri,"
    assert topic_tags("quốc hội họp", "", TOPICS) == ",chinh-tri,"  # không phân biệt hoa thường


def test_find_event_threshold():
    a, b = vec_to_blob([1.0, 0.0, 0.1]), vec_to_blob([0.0, 1.0, 0.0])
    cands = [("d1", "e1", a), ("d2", None, b)]
    assert find_event(vec_to_blob([0.9, 0.05, 0.1]), cands, 0.8)[0] == "e1"
    assert find_event(vec_to_blob([0.0, 0.95, 0.1]), cands, 0.8)[0] == "d2"  # chưa có event_id
    assert find_event(vec_to_blob([0.5, 0.5, 0.7]), cands, 0.8)[0] is None


STORM_VNE = "https://vnexpress.net/bao-so-12.html"
STORM_TT = "https://tuoitre.vn/bao-so-12.htm"
STORM_LEAD = "Bão số 12 đổ bộ Quảng Ngãi gây mưa lớn diện rộng, hàng nghìn hộ dân phải sơ tán."


@pytest.fixture
def multi(worker, components):
    s = components.settings
    s.news_topics = TOPICS
    s.news_event_threshold = 0.6
    s.news_feeds = {
        **s.news_feeds,
        "thoi-su": {"label": "Thời sự", "urls": ["https://vnexpress.net/rss/thoi-su.rss",
                                                  "https://tuoitre.vn/rss/thoi-su.rss"]},
    }  # fmt: skip
    body = "Chiều nay bão số 12 đổ bộ vào Quảng Ngãi với sức gió cấp 10, mưa lớn. " * 4
    worker._http.pages.update(
        {
            "https://vnexpress.net/rss/thoi-su.rss": rss(
                [("Bão số 12 đổ bộ Quảng Ngãi", STORM_VNE, NOW - timedelta(hours=1), STORM_LEAD)]
            ),
            "https://tuoitre.vn/rss/thoi-su.rss": rss(
                [("Bão số 12 đổ bộ Quảng Ngãi, mưa lớn", STORM_TT, NOW, STORM_LEAD)]
            ),
            STORM_VNE: page("Bão số 12 đổ bộ Quảng Ngãi", STORM_LEAD, [body]),
            STORM_TT: page("Bão số 12 đổ bộ Quảng Ngãi, mưa lớn", STORM_LEAD, [body]),
        }
    )
    worker.fetch_once()
    return worker


def test_same_story_from_two_papers_is_one_event(multi, components, make_client):
    arts = {a.url: a for a in components.db.list_articles()}
    assert arts[STORM_VNE].event_id == arts[STORM_TT].event_id
    assert arts[GOLD].event_id != arts[STORM_VNE].event_id
    assert arts[STORM_TT].source == "Tuổi Trẻ" and arts[STORM_TT].in_category("thoi-tiet")
    items = make_client(FakeLLM()).get("/api/news").json()
    storm = [i for i in items if i["sources_count"] == 2]
    assert len(items) == 3 and len(storm) == 1  # 4 bài -> 3 sự kiện
    assert {x["source"] for x in storm[0]["articles"]} == {"VnExpress", "Tuổi Trẻ"}


def test_topic_category_filter_and_brief(multi, make_client):
    client = make_client(FakeLLM())
    weather = client.get("/api/news?category=thoi-tiet").json()
    assert len(weather) == 1 and weather[0]["sources_count"] == 2
    cats = {c["id"]: c["count"] for c in client.get("/api/news/status").json()["categories"]}
    assert cats["thoi-tiet"] == 2 and cats["thoi-su"] == 2
    cid = client.get("/api/news/conversation").json()["id"]
    events = ask(client, cid, "tin thời tiết hôm nay")  # câu hỏi thời tiết: dự báo + tin bão
    assert of_type(events, "debug")[0]["mode"] == "weather"
    src = of_type(events, "sources")[0]["sources"]
    assert src[0]["document"].startswith("Dự báo thời tiết Hà Nội")
    assert [s["document"] for s in src[1:]] == ["Tuổi Trẻ: Bão số 12 đổ bộ Quảng Ngãi, mưa lớn"]
    events = ask(client, cid, "tin chính trị hôm nay")  # chuyên mục theo từ khoá: bản tin
    assert of_type(events, "debug")[0]["category"] == "chinh-tri"


def test_brief_puts_big_events_first(multi, make_client):
    fake = FakeLLM()
    client = make_client(fake)
    cid = client.get("/api/news/conversation").json()["id"]
    events = ask(client, cid, "hôm nay có tin gì?")
    sources = of_type(events, "sources")[0]["sources"]
    assert "2 báo đưa tin" in sources[0]["section"]  # sự kiện nhiều báo đưa được chọn đầu
    assert (
        len(sources) == 3
        and "2 báo cùng đưa tin (Tuổi Trẻ, VnExpress)" in (fake.last_messages[0]["content"])
    )


def test_backfill_tags_and_events_for_old_articles(multi, components):
    with components.lock:
        components.db.conn.execute("UPDATE articles SET tags=NULL, title_vec=NULL, event_id=NULL")
        components.db.conn.commit()
    while multi.backfill_once():
        pass
    arts = {a.url: a for a in components.db.list_articles()}
    assert arts[STORM_VNE].event_id == arts[STORM_TT].event_id
    assert arts[STORM_VNE].in_category("thoi-tiet")


# ---- thời tiết ----
from assistant.news.weather import WeatherService, is_weather_question  # noqa: E402
from tests.fakes import WEATHER_SAMPLE  # noqa: E402


def test_weather_question_detection():
    for q in ["thời tiết hôm nay thế nào", "chiều nay có mưa không", "mai mấy độ?",
              "có cần mang ô không", "dự báo cuối tuần"]:  # fmt: skip
        assert is_weather_question(q), q
    for q in ["hôm nay có tin gì", "giá vàng tăng bao nhiêu", "báo nào đưa tin vụ này"]:
        assert not is_weather_question(q), q


def test_weather_summary_and_description(components):
    w = WeatherService(components.settings, http_get=lambda url, params: WEATHER_SAMPLE)
    s = w.summary()
    assert s["location"] == "Hà Nội" and s["current"]["temp"] == 29 and s["current"]["feels"] == 33
    assert s["days"][0]["label"] == "Hôm nay" and s["days"][1]["label"] == "Thứ Ba"
    assert s["days"][5]["desc"] == "dông" and s["days"][5]["rain_prob"] == 90
    text = w.describe()
    assert "Hiện tại: 29°C, cảm giác như 33°C" in text and "13/10: 24–29°C, mưa nhỏ" in text
    assert "Các giờ còn lại hôm nay: 15h" in text


def test_weather_cached(components):
    calls = []
    w = WeatherService(components.settings, http_get=lambda u, p: calls.append(1) or WEATHER_SAMPLE)
    w.summary()
    w.describe()
    assert len(calls) == 1


def test_weather_chat_uses_real_forecast(make_client):
    fake = FakeLLM()
    client = make_client(fake)
    cid = client.get("/api/news/conversation").json()["id"]
    events = ask(client, cid, "Hà Nội chiều nay có mưa không?")
    assert of_type(events, "debug")[0]["mode"] == "weather"
    system = fake.last_messages[0]["content"]
    assert "Dự báo thời tiết Hà Nội (nguồn Open-Meteo" in system and "thời tiết" in system
    assert client.get("/api/news/weather").json()["current"]["icon"] == "☁️"


def test_weather_failure_tells_model_not_to_guess(make_client, components):
    from assistant.api import deps

    def boom(url, params):
        raise RuntimeError("mất mạng")

    fake = FakeLLM()
    client = make_client(fake)
    client.app.dependency_overrides[deps.get_weather] = lambda: WeatherService(
        components.settings, http_get=boom
    )
    cid = client.get("/api/news/conversation").json()["id"]
    ask(client, cid, "thời tiết hôm nay?")
    assert "Không lấy được dự báo" in fake.last_messages[0]["content"]
    assert client.get("/api/news/weather").status_code == 502


def test_docs_chat_does_not_answer_weather_from_forecast(make_client):
    fake = FakeLLM()
    client = make_client(fake)
    cid = client.post("/api/conversations").json()["id"]
    ask(client, cid, "thời tiết hôm nay thế nào?")
    assert "Open-Meteo" not in fake.last_messages[0]["content"]  # thời tiết thuộc khu Tin tức


def test_extract_strips_repeated_title_and_source_prefix():
    title = "Cánh đồng muối Atacama thành tâm điểm"
    lead = "(Dân trí) - Cánh đồng muối khổng lồ Atacama ở Chile đang trở thành tâm điểm toàn cầu."
    body = "".join(
        f"<p>Đoạn nội dung số {i}: khai thác lithium ở sa mạc Atacama tiêu tốn {i + 2} tỷ lít nước "
        f"mỗi năm, ảnh hưởng tới {i * 3 + 5} cộng đồng bản địa sống quanh vùng.</p>"
        for i in range(4)
    )
    html_doc = f"""<html><head><title>{title}</title><meta name="description" content="{lead}">
      </head><body><article><h1>{title}</h1><p>{lead}</p>{body}</article></body></html>"""
    art = extract_article(html_doc, "", "https://dantri.com.vn/the-gioi/x.htm")
    assert art.lead.startswith("Cánh đồng muối khổng lồ")
    assert art.text.startswith("Đoạn nội dung số 0") and "(Dân trí)" not in art.text


# ---- không làm web lag khi đang lấy tin ----
def test_embedding_runs_outside_lock(worker, components):
    """Embedding (chậm trên CPU) không giữ khoá CSDL: chat / thư viện không phải chờ."""
    held = []
    embed = components.embedder.embed_passages

    def spy(texts):
        held.append(components.lock.locked())
        return embed(texts)

    components.embedder.embed_passages = spy
    try:
        assert worker.fetch_once()["new"] == 2
    finally:
        components.embedder.embed_passages = embed
    assert held and not any(held)


def test_news_waits_while_user_is_chatting(worker, components):
    worker._s.news_yield_s = 0.3
    with components.activity.active():
        assert components.activity.idle_s() == 0
    t0 = time.monotonic()
    worker._wait_user_idle()  # vừa chat xong -> chờ đủ news_yield_s
    assert time.monotonic() - t0 >= 0.25
    assert components.activity.idle_s() >= 0.3


def test_status_counts_with_sql(worker, components):
    worker.fetch_once()
    total, pending, counts = components.db.article_stats()
    assert (total, pending) == (2, 2)
    assert counts["kinh-doanh"] == 1 and counts["giao-thong"] == 1
