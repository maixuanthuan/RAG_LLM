"""Khu Tin tức: nhận ra câu hỏi kiểu "hôm nay có tin gì" và dựng nguồn cho bản tin."""

import re
from datetime import datetime

from assistant.ingestion.text import fold
from assistant.news.events import group_events, n_sources, representative

NEWS_RULES = (
    "Bạn đang ở khu Tin tức: chỉ trả lời dựa trên các bài báo trong NGUỒN (đã lấy từ báo "
    "chính thống). Ghi [n] sau mỗi tin để người đọc mở bài gốc. Không có bài nào nói về điều "
    "được hỏi thì nói rõ là chưa thấy tin, không đoán. Khi đưa nhận định riêng thì nói rõ đó "
    "là nhận định, không phải nội dung báo."
)
WEATHER_RULES = (
    "Câu hỏi về thời tiết: trả lời bằng số liệu dự báo trong NGUỒN (nhiệt độ, khả năng mưa, "
    "gió), nói rõ đây là dự báo, có thể kèm lời khuyên ngắn (mang áo mưa, áo khoác...). Không "
    "đưa ra số liệu không có trong nguồn."
)
BRIEF_RULES = (
    "Viết BẢN TIN ngắn gọn từ các bài trong NGUỒN: nhóm theo chuyên mục (tên chuyên mục in "
    "đậm), chọn khoảng 6-10 tin quan trọng nhất, mỗi tin 1-2 câu kèm [n]. Mở đầu bằng một câu "
    "chào ngắn phù hợp thời điểm trong ngày."
)

_BRIEF = re.compile(
    r"(co tin gi|tin gi moi|co gi moi|tin tuc hom nay|tin hom nay|ban tin|diem tin|tin noi bat"
    r"|tin moi nhat|tin moi|hom nay co gi|chao buoi sang|chao ngay moi|tom tat tin|tong hop tin"
    r"|thoi su hom nay)"
)


def brief_category(question: str, feeds: dict[str, dict]) -> tuple[bool, str | None]:
    """(có phải câu hỏi bản tin, chuyên mục nếu có nhắc tới).

    "hôm nay có tin gì" -> (True, None); "tin kinh doanh hôm nay" -> (True, "kinh-doanh");
    "giá vàng hôm nay thế nào" -> (False, None) (câu hỏi cụ thể: tìm trong các bài).
    """
    q = fold(question)
    cat = next((c for c, cfg in feeds.items() if fold(cfg.get("label", c)) in q), None)
    is_brief = bool(_BRIEF.search(q)) or (
        cat is not None and re.search(r"\btin\b", q) is not None and len(q.split()) <= 8
    )
    return is_brief, cat


def ago(published_iso: str, now: datetime) -> str:
    try:
        dt = datetime.fromisoformat(published_iso)
    except ValueError:
        return ""
    mins = max(0, int((now - dt).total_seconds() // 60))
    if mins < 60:
        return f"{mins} phút trước"
    if mins < 24 * 60:
        return f"{mins // 60} giờ trước"
    return f"{dt:%H:%M %d/%m}"


def pick_brief(articles: list, budget_chars: int, per_item: int = 460) -> list[list]:
    """Chọn sự kiện cho bản tin (mỗi sự kiện = các bài cùng đưa một tin).

    Trong mỗi chuyên mục: sự kiện nhiều báo đưa tin xếp trước (độ quan trọng), rồi mới đến độ
    mới. Xoay vòng giữa các chuyên mục để chuyên mục nào cũng có mặt, chuyên mục có sự kiện
    lớn nhất được chọn trước. Dừng khi đủ độ dài.
    """
    by_cat: dict[str, list[list]] = {}
    for g in group_events(articles):  # articles đã sắp mới nhất trước
        by_cat.setdefault(representative(g).category, []).append(g)
    for groups in by_cat.values():
        groups.sort(key=n_sources, reverse=True)  # sort ổn định: cùng số báo thì giữ độ mới
    cats = sorted(by_cat, key=lambda c: n_sources(by_cat[c][0]), reverse=True)
    picked, total = [], 0
    while any(by_cat[c] for c in cats):
        for cat in cats:
            if not by_cat[cat]:
                continue
            g = by_cat[cat].pop(0)
            a = representative(g)
            size = min(per_item, len(a.title) + len(a.summary or a.lead or "") + 60)
            if picked and total + size > budget_chars:
                return picked
            picked.append(g)
            total += size
    return picked
