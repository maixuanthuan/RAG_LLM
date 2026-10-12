import re
from pathlib import Path

from assistant.ingestion.models import Block, ParseError
from assistant.ingestion.text import looks_like_heading, normalize_text


def _heading_level(style_name: str) -> int | None:
    name = (style_name or "").strip().lower()
    if name == "title":
        return 0
    if name.startswith("heading"):
        m = re.search(r"\d+", name)
        return int(m.group()) if m else 1
    return None


def _table_text(table) -> str:
    rows: list[list[str]] = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            value = normalize_text(cell.text).replace("\n", " ")
            if not cells or cells[-1] != value:  # ô gộp bị lặp lại trong python-docx
                cells.append(value)
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    header = rows[0]
    if len(rows) >= 2 and all(header):
        lines = []
        for row in rows[1:]:
            pairs = [f"{h}: {v}" for h, v in zip(header, row, strict=False) if v]
            if pairs:
                lines.append("; ".join(pairs))
        return "\n".join(lines)
    return "\n".join(" | ".join(c for c in row if c) for row in rows)


def parse_docx(path: Path) -> list[Block]:
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        doc = Document(str(path))
    except Exception as e:
        raise ParseError(f"Không mở được DOCX: {e}") from e

    body = list(doc.element.body.iterchildren())
    has_styled_headings = any(
        child.tag.endswith("}p") and _heading_level(Paragraph(child, doc).style.name) is not None
        for child in body
    )

    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []  # (cấp, tên mục) để tạo đường dẫn mục
    lines: list[str] = []

    def section() -> str | None:
        return " > ".join(t for _, t in stack) or None

    def flush() -> None:
        if lines:
            blocks.append(Block(text="\n".join(lines), page=None, section=section()))
            lines.clear()

    def set_heading(level: int, title: str) -> None:
        flush()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))

    for child in body:
        if child.tag.endswith("}p"):
            para = Paragraph(child, doc)
            text = normalize_text(para.text)
            if not text:
                continue
            level = _heading_level(para.style.name)
            if level is not None:
                set_heading(level, text)
            elif not has_styled_headings and looks_like_heading(text):
                set_heading(1, text)
            else:
                lines.append(text)
        elif child.tag.endswith("}tbl"):
            text = _table_text(Table(child, doc))
            if text:
                lines.extend(text.split("\n"))
    flush()
    return blocks
