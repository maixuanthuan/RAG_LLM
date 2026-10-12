"""Lấy tin từ báo: RSS để biết có bài gì mới, rồi vào từng bài lấy toàn văn.

- parse_rss / extract_article: hàm thuần (bytes -> dữ liệu), test được không cần mạng.
- HttpFetcher: header giống trình duyệt, tôn trọng robots.txt, nghỉ giữa các request,
  chỉ đi tới các tên miền có trong danh sách RSS (không đi theo link lạ).
"""

import html
import logging
import re
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from defusedxml import ElementTree

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}


@dataclass
class FeedItem:
    title: str
    url: str
    published: datetime  # luôn có múi giờ
    description: str


@dataclass
class Article:
    title: str
    text: str  # toàn văn đã làm sạch (các đoạn cách nhau bằng dòng trống)
    lead: str  # sapo / đoạn mở đầu


_TAG = re.compile(r"<[^>]+>")
_SOURCE_PREFIX = re.compile(r"^\((?:Dân trí|Dân Trí|PLO|VTV|TTXVN|Chinhphu\.vn|NLĐO)\)\s*[-–:]\s*")
_SPACES = re.compile(r"[ \t ]+")


def clean_text(s: str | None) -> str:
    s = html.unescape(_TAG.sub(" ", s or ""))
    return _SPACES.sub(" ", s).strip()


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s.strip())
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(s.strip())
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_rss(data: bytes) -> list[FeedItem]:
    """RSS 2.0 -> danh sách bài (mới nhất trước, bỏ trùng link)."""
    root = ElementTree.fromstring(data)
    items: dict[str, FeedItem] = {}
    for it in root.iter("item"):
        url = (it.findtext("link") or "").strip()
        title = clean_text(it.findtext("title"))
        if not url or not title:
            continue
        published = _parse_date(it.findtext("pubDate")) or datetime.now(timezone.utc)
        items.setdefault(
            url, FeedItem(title, url, published, clean_text(it.findtext("description")))
        )
    return sorted(items.values(), key=lambda x: x.published, reverse=True)


def extract_article(data: bytes | str, fallback_title: str = "", url: str = "") -> Article:
    """HTML bài báo -> tiêu đề, sapo, toàn văn.

    - VnExpress: bộ đọc riêng theo cấu trúc trang (h1.title-detail, p.description,
      article.fck_detail p.Normal), đã kiểm chứng.
    - Báo khác: trafilatura (thư viện chuyên lấy nội dung chính của trang báo, bỏ menu,
      quảng cáo, tin liên quan); nếu không được thì lấy các đoạn <p> đủ dài.
    """
    raw = data.encode() if isinstance(data, str) else data
    if b"fck_detail" not in raw and b"title-detail" not in raw:
        art = _extract_generic(raw, fallback_title, url)
        if len(art.text) >= 200:
            return art
    return _extract_by_layout(raw, fallback_title)


def _extract_generic(raw: bytes, fallback_title: str, url: str) -> Article:
    try:
        import trafilatura
    except ImportError:  # thiếu thư viện -> dùng cách đơn giản
        return Article(title=fallback_title, text="", lead="")
    html_text = raw.decode("utf-8", "ignore")
    text = (
        trafilatura.extract(
            html_text,
            url=url or None,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
            deduplicate=True,
        )
        or ""
    )
    meta = trafilatura.extract_metadata(html_text)
    title = clean_text(meta.title) if meta and meta.title else fallback_title
    lead = _SOURCE_PREFIX.sub("", clean_text(meta.description)) if meta and meta.description else ""
    paras = [clean_text(p) for p in text.split("\n") if clean_text(p)]
    # bỏ tiêu đề / sapo bị lặp ở đầu nội dung (vd Dân trí: "Tiêu đề", "(Dân trí) - Sapo...")
    while paras and (
        (title and paras[0].startswith(title[:60]))
        or (lead and _SOURCE_PREFIX.sub("", paras[0]).startswith(lead[:60]))
    ):
        paras = paras[1:]
    return Article(title=title or fallback_title, text="\n\n".join(paras), lead=lead)


def _extract_by_layout(raw: bytes, fallback_title: str) -> Article:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "figure", "figcaption", "table"]):
        tag.decompose()
    h1 = soup.select_one("h1.title-detail") or soup.find("h1")
    title = clean_text(h1.get_text(" ")) if h1 else fallback_title
    lead_el = soup.select_one("p.description") or soup.select_one(".sapo, .detail-sapo")
    lead = clean_text(lead_el.get_text(" ")) if lead_el else ""
    body = soup.select_one("article.fck_detail") or soup.find("article") or soup
    paras = body.select("p.Normal") or body.find_all("p")
    texts = []
    for p in paras:
        if lead_el is not None and p is lead_el:
            continue
        t = clean_text(p.get_text(" "))
        if len(t) >= 40 or (p.get("class") and "Normal" in p.get("class")):
            texts.append(t)
    return Article(title=title or fallback_title, text="\n\n".join(texts), lead=lead)


class HttpFetcher:
    """Tải trang có giãn cách, tôn trọng robots.txt, chỉ cho phép các tên miền đã khai báo."""

    def __init__(self, allowed_hosts: set[str], delay_s: float = 1.0, timeout_s: float = 20):
        import httpx

        self._client = httpx.Client(
            headers=HEADERS, timeout=timeout_s, follow_redirects=True, max_redirects=5
        )
        self._allowed = {h.lower() for h in allowed_hosts}
        self._delay = delay_s
        self._last = 0.0
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def close(self) -> None:
        self._client.close()

    def allowed_host(self, url: str) -> bool:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        return p.scheme in ("http", "https") and any(
            host == h or host.endswith("." + h) for h in self._allowed
        )

    def _get(self, url: str) -> bytes:
        wait = self._delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = self._client.get(url)
        finally:
            self._last = time.monotonic()
        r.raise_for_status()
        return r.content

    def robots_allows(self, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                rp.parse(self._get(base + "/robots.txt").decode("utf-8", "ignore").splitlines())
                self._robots[base] = rp
            except Exception as e:  # không đọc được robots.txt -> coi như không cấm
                log.info("không đọc được robots.txt của %s: %s", base, e)
                self._robots[base] = None
        rp = self._robots[base]
        return rp is None or rp.can_fetch(USER_AGENT, url)

    def get(self, url: str) -> bytes:
        if not self.allowed_host(url):
            raise PermissionError(f"Tên miền không nằm trong danh sách báo: {url}")
        if not self.robots_allows(url):
            raise PermissionError(f"robots.txt không cho phép: {url}")
        return self._get(url)
