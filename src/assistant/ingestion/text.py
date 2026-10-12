"""Chuẩn hoá văn bản, bỏ dấu tiếng Việt, nhận diện tiêu đề."""

import re
import unicodedata

_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_text(text: str) -> str:
    """NFC, bỏ ký tự vô hình/điều khiển, gọn khoảng trắng."""
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_INVISIBLE)
    text = text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\t", " ")
    text = _CONTROL.sub("", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fold(text: str) -> str:
    """Chữ thường, bỏ dấu (đ -> d). Dùng cho tìm từ khoá không phân biệt dấu."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.replace("đ", "d")


_KEYWORD_HEADING = re.compile(r"^(dieu|chuong|muc|phan|phu luc)\s+([ivxlc]+|\d+)\b")
_NUMBERED = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2}){1,3}|\d{1,2}[.)])\s+")
_ROMAN = re.compile(r"^(?:I|II|III|IV|V|VI|VII|VIII|IX|X)[.)]\s+")


def looks_like_heading(line: str) -> bool:
    """Heuristic nhận tiêu đề trong văn bản thuần (PDF, TXT).

    Nhận: "Điều 5. ...", "Chương II", "1.2 Tên mục", "II. TÊN MỤC", dòng in hoa ngắn.
    """
    line = line.strip()
    if not line or len(line) > 100 or line.endswith((",", ";")):
        return False
    if _KEYWORD_HEADING.match(fold(line)):
        return True
    if line.endswith("."):  # câu thường, không phải tiêu đề đánh số
        return False
    for pattern in (_NUMBERED, _ROMAN):
        m = pattern.match(line)
        if m and line[m.end() :][:1].isupper():
            return True
    letters = [c for c in line if c.isalpha()]
    if len(letters) >= 4 and len(line.split()) >= 2 and len(line) <= 80:
        upper = sum(1 for c in letters if c.isupper())
        if upper / len(letters) > 0.85:
            return True
    return False
