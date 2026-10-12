from dataclasses import dataclass, field
from typing import Literal

from assistant.core.config import Settings
from assistant.rag.db import MetadataStore
from assistant.rag.embedder import Embedder
from assistant.rag.fusion import rrf
from assistant.rag.vectors import QdrantStore

Mode = Literal["vector", "keyword", "hybrid"]


@dataclass
class SearchHit:
    chunk_id: str
    score: float
    document: str
    title: str
    document_id: str
    page: int | None
    section: str | None
    department: str
    text: str
    ord: int = -1  # thứ tự chunk trong tài liệu (để lấy thêm đoạn kề)
    ranks: dict[str, int] = field(default_factory=dict)  # hạng ở từng bộ tìm
    vector_score: float | None = None  # cosine với câu hỏi (None nếu không lọt top vector)


class Retriever:
    def __init__(
        self, settings: Settings, db: MetadataStore, embedder: Embedder, vectors: QdrantStore
    ) -> None:
        self._s = settings
        self._db = db
        self._embedder = embedder
        self._vectors = vectors

    def search(
        self,
        query: str,
        top_k: int | None = None,
        mode: Mode = "hybrid",
        departments: list[str] | None = None,
        document_ids: list[str] | None = None,
        exclude_departments: list[str] | None = None,
    ) -> list[SearchHit]:
        """Tìm đoạn liên quan.

        `departments` giới hạn phạm vi tài liệu được phép truy cập, `document_ids` giới hạn
        trong các tài liệu người dùng đang ghim (chế độ tập trung).
        """
        top_k = top_k or self._s.retrieval_top_k
        n = max(self._s.retrieval_candidates, top_k)

        vec: list[tuple[str, float]] = []
        kw: list[tuple[str, float]] = []
        if mode in ("vector", "hybrid"):
            vec = self._vectors.search(
                self._embedder.embed_query(query), n, departments, document_ids, exclude_departments
            )
        if mode in ("keyword", "hybrid"):
            kw = self._db.keyword_search(query, n, departments, document_ids, exclude_departments)

        ranks: dict[str, dict[str, int]] = {}
        for name, results in (("vector", vec), ("keyword", kw)):
            for r, (cid, _) in enumerate(results, start=1):
                ranks.setdefault(cid, {})[name] = r

        if mode == "vector":
            ordered = vec
        elif mode == "keyword":
            ordered = kw
        else:
            ordered = rrf(
                {"vector": [c for c, _ in vec], "keyword": [c for c, _ in kw]}, self._s.rrf_k
            )
        ordered = ordered[:top_k]

        vec_scores = dict(vec)
        records = self._db.get_chunks([c for c, _ in ordered])
        hits: list[SearchHit] = []
        for cid, score in ordered:
            rec = records.get(cid)
            if rec is None:  # chunk đã bị xoá khỏi SQLite nhưng còn vector mồ côi
                continue
            hits.append(
                SearchHit(
                    chunk_id=cid,
                    score=score,
                    document=rec.document,
                    title=rec.title,
                    document_id=rec.document_id,
                    page=rec.page,
                    section=rec.section,
                    department=rec.department,
                    text=rec.text,
                    ord=rec.ord,
                    ranks=ranks.get(cid, {}),
                    vector_score=vec_scores.get(cid),
                )
            )
        return hits
