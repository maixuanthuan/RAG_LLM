from dataclasses import dataclass, field


class ParseError(Exception):
    """File không đọc được (hỏng, có mật khẩu, định dạng không hỗ trợ)."""


@dataclass
class Block:
    """Một đoạn văn bản liền mạch cùng vị trí gốc, dùng cho trích dẫn."""

    text: str
    page: int | None = None
    section: str | None = None


@dataclass
class ParsedDocument:
    title: str
    blocks: list[Block]
    pages: int | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class ChunkDraft:
    ord: int
    text: str
    page: int | None
    section: str | None
