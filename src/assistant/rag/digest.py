"""Đọc toàn bộ tài liệu dài theo từng phần để không bỏ sót nội dung.

Giống xử lý ảnh gigapixel: không đưa cả tài liệu vào LLM một lần (vượt context), mà chia
thành các phần vừa context, đọc lần lượt từng phần rồi tổng hợp.

- Lúc nạp (chạy nền): mỗi phần -> ghi chú chi tiết (level 1); gộp dần các ghi chú
  (level 2, 3, ...) đến khi còn một bản tổng quan (level 0).
- Lúc hỏi: câu tổng quát dùng ghi chú của mọi phần; câu cần quét hết ("liệt kê tất cả")
  đọc lại nguyên văn từng phần với câu hỏi đó rồi tổng hợp.
"""

import re
from dataclasses import dataclass

from assistant.ingestion.text import fold
from assistant.rag.db import ChunkRecord

NONE_MARK = "KHÔNG CÓ"

NOTES_SYSTEM = (
    "Bạn đang đọc một phần của một tài liệu dài để ghi chú lại, sao cho sau này chỉ cần đọc "
    "ghi chú vẫn trả lời được câu hỏi về phần này. Ghi chú CHI TIẾT và TRUNG THỰC: ý chính, "
    "luận điểm, số liệu, tên riêng, định nghĩa, ví dụ, danh sách, kết luận. Sau mỗi ý ghi số "
    "trang dạng (tr. X) nếu có. Chỉ dùng thông tin trong phần được đưa, không thêm kiến thức "
    "ngoài, không bình luận. Viết gạch đầu dòng bằng tiếng Việt."
)

REDUCE_SYSTEM = (
    "Dưới đây là ghi chú của các phần liên tiếp trong một tài liệu. Gộp thành một bản ghi chú "
    "ngắn gọn hơn nhưng không bỏ ý quan trọng nào: giữ cấu trúc theo phần, số liệu, tên riêng "
    "và số trang (tr. X). Không thêm thông tin ngoài ghi chú. Viết gạch đầu dòng bằng tiếng Việt."
)

OVERVIEW_SYSTEM = (
    "Dưới đây là ghi chú của toàn bộ một tài liệu. Viết bản TỔNG QUAN: tài liệu nói về gì, "
    "dành cho ai, cấu trúc gồm những phần nào (kèm khoảng trang), ý chính của từng phần, kết "
    "luận hoặc thông điệp chính. Không thêm thông tin ngoài ghi chú. Viết bằng tiếng Việt."
)

SCAN_SYSTEM = (
    "Bạn đọc một phần của tài liệu để tìm thông tin cho câu hỏi. Trích ra MỌI thông tin trong "
    "phần này liên quan đến câu hỏi, giữ nguyên số liệu, tên riêng và ghi (tr. X) sau mỗi ý. "
    f"Không trả lời câu hỏi, chỉ trích. Nếu phần này không có gì liên quan, chỉ viết: {NONE_MARK}"
)


@dataclass
class Part:
    idx: int  # 1-based
    text: str  # nguyên văn, có đánh dấu [tr. X] khi sang trang mới
    page_from: int | None
    page_to: int | None

    def label(self, total: int) -> str:
        return f"phần {self.idx}/{total}" + (f" ({page_range(self)})" if self.page_from else "")


def page_range(obj) -> str:
    if obj.page_from is None:
        return ""
    if obj.page_to is None or obj.page_to == obj.page_from:
        return f"tr. {obj.page_from}"
    return f"tr. {obj.page_from}–{obj.page_to}"


def _overlap(prev: list[str], cur: list[str]) -> int:
    k = min(len(prev), len(cur))
    while k > 0 and prev[-k:] != cur[:k]:
        k -= 1
    return k


def split_parts(chunks: list[ChunkRecord], part_chars: int) -> list[Part]:
    """Chia nguyên văn (theo thứ tự chunk, bỏ phần chồng lấp) thành các phần <= part_chars."""
    parts: list[Part] = []
    lines: list[str] = []
    pages: list[int] = []
    size = 0
    prev_lines: list[str] = []
    prev_section: object = object()
    prev_page: int | None = None

    def flush() -> None:
        nonlocal lines, pages, size
        if lines:
            parts.append(
                Part(
                    idx=len(parts) + 1,
                    text="\n".join(lines).strip(),
                    page_from=min(pages) if pages else None,
                    page_to=max(pages) if pages else None,
                )
            )
        lines, pages, size = [], [], 0

    for c in chunks:
        cur = c.text.split("\n")
        new_section = c.section != prev_section
        if new_section:
            prev_lines = []
        new = cur[_overlap(prev_lines, cur) :]
        prev_lines, prev_section = cur, c.section
        block = []
        if c.page is not None and c.page != prev_page:
            block.append(f"[tr. {c.page}]")
            prev_page = c.page
        if c.section and new_section:
            block.append(f"## {c.section}")
        block += new
        block_len = sum(len(x) + 1 for x in block)
        if size and size + block_len > part_chars:
            flush()
            if c.page is not None and (not block or not block[0].startswith("[tr.")):
                block.insert(0, f"[tr. {c.page}]")
        lines += block
        size += block_len
        if c.page is not None:
            pages.append(c.page)
    flush()
    return parts


def batch_texts(items: list[str], max_chars: int) -> list[list[int]]:
    """Gom chỉ số các đoạn liên tiếp thành từng lô có tổng độ dài <= max_chars."""
    batches: list[list[int]] = []
    cur: list[int] = []
    size = 0
    for i, t in enumerate(items):
        if cur and size + len(t) > max_chars:
            batches.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(t)
    if cur:
        batches.append(cur)
    return batches


def notes_messages(title: str, part: Part, total: int) -> list[dict]:
    return [
        {"role": "system", "content": NOTES_SYSTEM},
        {"role": "user", "content": f"Tài liệu: {title}\n{part.label(total)}:\n\n{part.text}"},
    ]


def reduce_messages(title: str, notes: list[str], final: bool) -> list[dict]:
    return [
        {"role": "system", "content": OVERVIEW_SYSTEM if final else REDUCE_SYSTEM},
        {"role": "user", "content": f"Tài liệu: {title}\n\n" + "\n\n".join(notes)},
    ]


def scan_messages(question: str, title: str, part: Part, total: int) -> list[dict]:
    return [
        {"role": "system", "content": SCAN_SYSTEM},
        {
            "role": "user",
            "content": f"Câu hỏi: {question}\n\nTài liệu: {title}\n{part.label(total)}:\n\n"
            f"{part.text}",
        },
    ]


def is_empty_extract(text: str) -> bool:
    t = fold(text.strip().strip(".:").strip())
    return not t or t.startswith(fold(NONE_MARK)) and len(t) < 40


# ---- phân loại câu hỏi về tài liệu dài ----
_SCAN = re.compile(
    r"\b(liet ke|tat ca|toan bo|het cac|moi (?:cho|lan|vi du|phan)|bao nhieu (?:lan|vi du|cho)"
    r"|dem (?:xem|so)|nhung cho nao|o dau trong tai lieu|day du)\b"
)
_OVERVIEW = re.compile(
    r"\b(tom tat|tom luoc|tong quan|tong ket|y chinh|noi dung chinh|y tuong chinh|thong diep"
    r"|danh gia|nhan xet|phan tich (?:tong|toan|tai lieu|bai)|bai hoc|ket luan|cau truc|muc luc"
    r"|noi ve (?:gi|cai gi|van de gi)|gioi thieu (?:tai lieu|bai)"
    r"|diem (?:manh|yeu|bat loi|noi bat))\b"
)


def classify_question(question: str) -> str:
    """overview (cần cả tài liệu) | scan (phải quét hết) | specific (tìm đoạn liên quan).

    Dùng quy tắc từ khoá để nhanh và dễ debug (hiện trong ⓘ chi tiết).
    """
    q = fold(question)
    if _SCAN.search(q):
        return "scan"
    if _OVERVIEW.search(q):
        return "overview"
    return "specific"
