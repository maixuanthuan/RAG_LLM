"""Cắt văn bản thành chunk theo cấu trúc: không vượt qua ranh giới mục."""

import re

from assistant.ingestion.models import Block, ChunkDraft

_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")


def _split_long(text: str, size: int) -> list[str]:
    """Chia một dòng quá dài: theo câu trước, rồi cắt cứng ở khoảng trắng."""
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        sentence = sentence.strip()
        while len(sentence) > size:
            cut = sentence.rfind(" ", 0, size)
            if cut < size * 0.5:
                cut = size
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            pieces.append(sentence)
    merged: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) <= size:
            current += " " + piece
        else:
            if current:
                merged.append(current)
            current = piece
    if current:
        merged.append(current)
    return merged


def _joined_len(units: list[tuple[str, int | None]]) -> int:
    return sum(len(u[0]) for u in units) + max(len(units) - 1, 0)


def chunk_blocks(blocks: list[Block], size: int = 1200, overlap: int = 200) -> list[ChunkDraft]:
    """Gộp các dòng liên tiếp trong cùng một mục thành chunk <= size ký tự.

    Mỗi chunk giữ page của dòng đầu tiên và section của mục chứa nó. Giữa hai chunk
    liền kề trong cùng mục có phần chồng lấp (tối đa `overlap` ký tự, theo dòng).
    """
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("Cần size > 0 và 0 <= overlap < size")

    groups: list[tuple[str | None, list[Block]]] = []
    for block in blocks:
        if groups and groups[-1][0] == block.section:
            groups[-1][1].append(block)
        else:
            groups.append((block.section, [block]))

    chunks: list[ChunkDraft] = []

    def emit(units: list[tuple[str, int | None]], section: str | None) -> None:
        chunks.append(
            ChunkDraft(
                ord=len(chunks),
                text="\n".join(u[0] for u in units),
                page=units[0][1],
                section=section,
            )
        )

    for section, group in groups:
        units: list[tuple[str, int | None]] = []
        for block in group:
            for line in block.text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                for piece in _split_long(line, size):
                    units.append((piece, block.page))

        buf: list[tuple[str, int | None]] = []
        fresh = 0  # số dòng mới (không tính phần chồng lấp) trong buf
        for unit in units:
            if buf and _joined_len(buf) + 1 + len(unit[0]) > size:
                if fresh > 0:
                    emit(buf, section)
                carry: list[tuple[str, int | None]] = []
                for prev in reversed(buf):
                    if _joined_len([prev, *carry]) > overlap:
                        break
                    carry.insert(0, prev)
                if carry and _joined_len(carry) + 1 + len(unit[0]) > size:
                    carry = []
                buf = carry
                fresh = 0
            buf.append(unit)
            fresh += 1
        if buf and fresh > 0:
            emit(buf, section)

    return chunks
