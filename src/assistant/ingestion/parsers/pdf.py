import re
from collections import Counter
from pathlib import Path

from assistant.ingestion.models import Block, ParseError
from assistant.ingestion.text import looks_like_heading, normalize_text

_PAGE_NUMBER = re.compile(r"^(trang\s+)?\d{1,4}(\s*/\s*\d{1,4})?$", re.IGNORECASE)
_SENTENCE_END = (".", ":", ";", "!", "?")


def _strip_repeated(pages_lines: list[list[str]]) -> list[list[str]]:
    """Bỏ đầu trang/chân trang lặp lại và số trang."""
    n = len(pages_lines)
    counts: Counter[str] = Counter()
    for lines in pages_lines:
        counts.update(set(lines))
    repeated = {
        line for line, c in counts.items() if n >= 3 and c >= max(3, 0.6 * n) and len(line) < 80
    }
    return [
        [ln for ln in lines if ln not in repeated and not _PAGE_NUMBER.match(ln)]
        for lines in pages_lines
    ]


def parse_pdf(path: Path) -> tuple[list[Block], int, list[str]]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ParseError("PDF có mật khẩu, không đọc được")
        pages = list(reader.pages)
    except ParseError:
        raise
    except Exception as e:
        raise ParseError(f"Không mở được PDF: {e}") from e

    warnings: list[str] = []
    pages_lines: list[list[str]] = []
    for i, page in enumerate(pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception as e:
            warnings.append(f"Trang {i}: lỗi đọc chữ ({e})")
            raw = ""
        lines = [ln.strip() for ln in normalize_text(raw).split("\n") if ln.strip()]
        if len("".join(lines)) < 20:
            warnings.append(f"Trang {i}: gần như không có chữ (có thể là bản scan, cần OCR)")
        pages_lines.append(lines)

    blocks: list[Block] = []
    section: str | None = None
    for page_no, lines in enumerate(_strip_repeated(pages_lines), start=1):
        units: list[str] = []
        for line in lines:
            if looks_like_heading(line):
                _flush(blocks, units, page_no, section)
                section = line
            elif units and not units[-1].endswith(_SENTENCE_END) and line[0].islower():
                units[-1] += " " + line  # nối dòng bị ngắt cứng giữa câu
            else:
                units.append(line)
        _flush(blocks, units, page_no, section)
    return blocks, len(pages), warnings


def _flush(blocks: list[Block], units: list[str], page: int, section: str | None) -> None:
    if units:
        blocks.append(Block(text="\n".join(units), page=page, section=section))
        units.clear()
