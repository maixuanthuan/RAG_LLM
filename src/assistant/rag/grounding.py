"""Dựng ngữ cảnh nguồn cho LLM: prompt bám nguồn, viết lại câu hỏi nối tiếp, kiểm tra số liệu."""

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from assistant.ingestion.text import fold
from assistant.rag.db import ChunkRecord

GROUNDING_RULES = """Bên dưới là NGUỒN người dùng đã cung cấp (tài liệu, bài báo), \
đánh số [1], [2], ...
Quy tắc trả lời:
1. Câu hỏi về nội dung nguồn: chỉ dựa vào nguồn. Ghi số nguồn ngay sau mỗi dữ kiện lấy từ \
nguồn, ví dụ: "Nhân viên chính thức được nghỉ 12 ngày phép mỗi năm [1]."
2. Không bịa: nếu nguồn không có thông tin cần thiết thì nói rõ "Nguồn không đề cập đến ...", \
không đoán, và không gắn số nguồn vào câu đó. Không đưa ra số liệu, tên, ngày tháng không có \
trong nguồn.
3. Được phân tích, giải thích, so sánh, đánh giá nội dung nguồn; phần nào là nhận định của bạn \
thì nói rõ đó là nhận định.
4. Câu hỏi không liên quan đến nguồn (chào hỏi, kiến thức chung): trả lời bình thường, không \
trích dẫn. Nếu bổ sung kiến thức ngoài nguồn thì ghi rõ "(ngoài nguồn)"."""

FOCUS_NOTE = (
    "Người dùng đang trao đổi về đúng các nguồn dưới đây. Coi đây là tài liệu đang mở trước mặt "
    "cả hai: câu hỏi như 'bài này', 'tài liệu này', 'đoạn đó' là nói về các nguồn này."
)

REWRITE_SYSTEM = (
    "Nhiệm vụ: viết lại câu hỏi cuối của người dùng thành MỘT câu hỏi độc lập, đủ ý để tìm kiếm "
    "trong tài liệu (thay 'nó', 'thế', 'cái đó', ... bằng đối tượng cụ thể đã nói trong "
    "hội thoại). "
    "Chỉ trả về câu hỏi đã viết lại. Không giải thích, không trả lời câu hỏi."
)


@dataclass
class Source:
    n: int  # số thứ tự dùng để trích dẫn [n]
    document_id: str
    document: str  # tên file
    title: str
    text: str
    page: int | None = None
    section: str | None = None
    full: bool = False  # True = toàn văn tài liệu, False = một đoạn tìm được
    score: float | None = None  # độ giống với câu hỏi (cosine), để debug/chỉnh ngưỡng
    url: str | None = None  # bài báo: link bài gốc

    def label(self) -> str:
        parts = [self.document]
        if self.section and not self.full:
            parts.append(f"mục: {self.section}")
        if self.page and not self.full:
            parts.append(f"trang {self.page}")
        if self.full:
            parts.append("toàn văn")
        return " | ".join(parts)

    def ref(self) -> dict:
        """Thông tin trích dẫn gửi cho client (không kèm nội dung)."""
        return {
            "n": self.n,
            "document_id": self.document_id,
            "document": self.document,
            "title": self.title,
            "page": self.page,
            "section": self.section,
            "full": self.full,
            "score": round(self.score, 3) if self.score is not None else None,
            "url": self.url,
        }


_WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def now_line(tz: str, now: datetime | None = None) -> str:
    """Câu báo thời điểm hiện tại cho LLM (model không tự biết hôm nay là ngày nào)."""
    now = now or datetime.now(ZoneInfo(tz))
    return f"Thời điểm hiện tại: {_WEEKDAYS[now.weekday()]}, {now:%d/%m/%Y %H:%M} ({tz})."


def _overlap(prev: list[str], cur: list[str]) -> int:
    """Số dòng đầu của `cur` trùng với đuôi của `prev` (phần chồng lấp khi chunk)."""
    k = min(len(prev), len(cur))
    while k > 0 and prev[-k:] != cur[:k]:
        k -= 1
    return k


def merge_chunks(chunks: list[ChunkRecord]) -> str:
    """Dựng lại toàn văn tài liệu từ các chunk: bỏ phần chồng lấp, giữ tên mục."""
    out: list[str] = []
    prev_lines: list[str] = []
    prev_section: object = object()
    for c in chunks:
        lines = c.text.split("\n")
        if c.section != prev_section:
            if c.section:
                out.append(f"\n## {c.section}")
            prev_lines = []
        out.extend(lines[_overlap(prev_lines, lines) :])
        prev_lines, prev_section = lines, c.section
    return "\n".join(out).strip()


def build_system_prompt(base: str, sources: list[Source], focus: bool) -> str:
    parts = [base, GROUNDING_RULES]
    if focus:
        parts.append(FOCUS_NOTE)
    parts.append("NGUỒN:")
    parts += [f"[{s.n}] {s.label()}\n{s.text}" for s in sources]
    return "\n\n".join(parts)


def rewrite_messages(history: list[dict], question: str) -> list[dict]:
    lines = []
    for m in history[-4:]:
        who = "Người dùng" if m["role"] == "user" else "Trợ lý"
        lines.append(f"{who}: {m['content'][:400]}")
    user = "Hội thoại:\n" + "\n".join(lines) + f"\n\nCâu hỏi cuối: {question}\n\nCâu hỏi độc lập:"
    return [{"role": "system", "content": REWRITE_SYSTEM}, {"role": "user", "content": user}]


def clean_rewrite(text: str, fallback: str) -> str:
    """Lấy dòng đầu của kết quả viết lại; kết quả bất thường thì dùng câu hỏi gốc."""
    for line in text.strip().split("\n"):
        line = line.strip().strip('"“”').strip()
        if line.lower().startswith("câu hỏi độc lập:"):
            line = line.split(":", 1)[1].strip()
        if line:
            return line if len(line) <= 300 else fallback
    return fallback


_NUMBER = re.compile(r"\d+(?:[.,:/]\d+)*")
_CITATION = re.compile(r"\[\d+(?:\s*,\s*\d+)*\]")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def unsupported_numbers(answer: str, evidence: str) -> list[str]:
    """Các con số (>= 2 chữ số) trong câu trả lời mà không xuất hiện trong nguồn/câu hỏi.

    So theo dãy chữ số nên "1.200.000" khớp "1,200,000". Đây chỉ là cảnh báo rẻ để bắt
    lỗi bịa số liệu, không chứng minh câu trả lời đúng.
    """
    known = {_digits(m) for m in _NUMBER.findall(evidence)}
    bad: list[str] = []
    for m in _NUMBER.findall(_CITATION.sub("", answer)):
        d = _digits(m)
        if len(d) >= 2 and d not in known and m not in bad:
            bad.append(m)
    return bad


# ---- nhiều tài liệu trong một cuộc trò chuyện: câu hỏi đang nói về tài liệu nào? ----
_DEICTIC = re.compile(
    r"\b(file|tai lieu|bai|bai bao|van ban|tep|bao cao|hop dong|noi dung)\s+"
    r"(nay|do|vua (?:gui|dua|up|tai|dinh kem)|moi (?:gui|nhat|dinh kem)|cuoi)\b"
)
_COMMON = {"file", "tai", "lieu", "ban", "bao", "cao", "moi", "nhat", "quy", "dinh", "phien"}


def _mentions(q: str, filename: str) -> bool:
    stem = re.sub(r"\.[a-z0-9]+$", "", filename, flags=re.I)
    words = re.findall(r"[A-Za-zÀ-ỹĐđ0-9]+", stem.replace("_", " "))
    folded = [fold(w) for w in words]
    if len(folded) >= 2 and " ".join(folded) in q:
        return True
    # cụm 2 từ liên tiếp của tên file, hoặc từ viết tắt in hoa (BOM, CNTT, SIHUB, ...)
    for a, b in zip(folded, folded[1:], strict=False):
        if f"{a} {b}" in q and not {a, b} <= _COMMON:
            return True
    return any(w.isupper() and len(w) >= 3 and re.search(rf"\b{fold(w)}\b", q) for w in words)


def pick_documents(question: str, docs: list) -> tuple[list, str]:
    """docs theo thứ tự đính kèm (cuối = mới nhất), mỗi phần tử có .filename (hoặc (doc, ...)).

    - câu hỏi nêu tên tài liệu -> các tài liệu đó;
    - "file này", "tài liệu vừa gửi", ... -> tài liệu mới nhất;
    - còn lại -> tất cả.
    """
    if len(docs) <= 1:
        return docs, "tài liệu duy nhất"

    def name(d):
        return (d[0] if isinstance(d, tuple) else d).filename

    q = fold(question)
    named = [d for d in docs if _mentions(q, name(d))]
    if named:
        return named, "câu hỏi nêu tên tài liệu"
    if _DEICTIC.search(q):
        return [docs[-1]], "nói 'file này' -> tài liệu đính kèm gần nhất"
    return docs, "dùng tất cả tài liệu đính kèm"
