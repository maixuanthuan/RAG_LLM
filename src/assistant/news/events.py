"""Chuyên mục theo từ khoá và gom các bài cùng một sự kiện (nhiều báo cùng đưa tin)."""

import re

import numpy as np


def topic_tags(title: str, lead: str | None, topics: dict[str, dict]) -> str:
    """Nhãn chuyên mục theo từ khoá, dạng ",thoi-tiet,chinh-tri," ("," nếu không có).

    So khớp CÓ DẤU, nguyên từ, không phân biệt hoa thường: "bão" không khớp "báo".
    """
    text = f"{title} {lead or ''}".lower()
    tags = [
        tid
        for tid, cfg in topics.items()
        if any(
            re.search(rf"(?<!\w){re.escape(kw.lower())}(?!\w)", text)
            for kw in cfg.get("keywords", [])
        )
    ]
    return "," + ",".join(tags) + ("," if tags else "")


def vec_to_blob(vec: list[float]) -> bytes:
    v = np.asarray(vec, dtype=np.float32)
    v /= np.linalg.norm(v) or 1.0
    return v.astype(np.float16).tobytes()


def blob_to_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float16).astype(np.float32)


def find_event(
    vec_blob: bytes, candidates: list[tuple[str, str | None, bytes]], threshold: float
) -> tuple[str | None, float]:
    """Sự kiện của bài mới: bài cũ giống nhất (cosine tiêu đề+sapo) nếu >= threshold.

    candidates: (document_id, event_id, title_vec). Trả về (event_id hoặc None, độ giống).
    """
    if not candidates:
        return None, 0.0
    v = blob_to_vec(vec_blob)
    mat = np.stack([blob_to_vec(b) for _, _, b in candidates])
    scores = mat @ v
    i = int(np.argmax(scores))
    best = float(scores[i])
    if best < threshold:
        return None, best
    doc_id, event_id, _ = candidates[i]
    return event_id or doc_id, best


def group_events(articles: list) -> list[list]:
    """Gom các bài (đã sắp mới nhất trước) theo event_id, giữ thứ tự xuất hiện."""
    groups: dict[str, list] = {}
    for a in articles:
        groups.setdefault(a.event_id or a.document_id, []).append(a)
    return list(groups.values())


def representative(group: list):
    """Bài đại diện cho sự kiện: bài mới nhất đã có tóm tắt (nếu có)."""
    return next((a for a in group if a.summary), group[0])


def n_sources(group: list) -> int:
    return len({a.source for a in group})
