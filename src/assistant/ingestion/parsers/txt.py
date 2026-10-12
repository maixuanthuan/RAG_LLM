from pathlib import Path

from assistant.ingestion.models import Block, ParseError
from assistant.ingestion.text import looks_like_heading, normalize_text

_ENCODINGS = ("utf-8-sig", "utf-16", "cp1258", "latin-1")


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    raise ParseError("Không đọc được mã hoá ký tự của file")


def parse_txt(path: Path) -> list[Block]:
    text = normalize_text(read_text(path))
    is_markdown = path.suffix.lower() == ".md"
    blocks: list[Block] = []
    section: str | None = None
    lines: list[str] = []

    def flush() -> None:
        if lines:
            blocks.append(Block(text="\n".join(lines), page=None, section=section))
            lines.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if is_markdown and stripped.startswith("#"):
            flush()
            section = stripped.lstrip("#").strip() or section
            continue
        if not is_markdown and looks_like_heading(stripped):
            flush()
            section = stripped
            continue
        lines.append(stripped)
    flush()
    return blocks
