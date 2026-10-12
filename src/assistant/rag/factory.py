import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from assistant.core.config import Settings, get_settings
from assistant.ingestion.pipeline import IngestionPipeline
from assistant.rag.db import MetadataStore
from assistant.rag.embedder import Embedder, OpenAICompatEmbedder
from assistant.rag.retriever import Retriever
from assistant.rag.vectors import QdrantStore


class UserActivity:
    """Người dùng đang chat / tải file? Việc nền (lấy tin, tóm tắt tin) nhường máy cho họ."""

    def __init__(self) -> None:
        self._n = 0
        self._last = float("-inf")
        self._mu = threading.Lock()

    @contextmanager
    def active(self):
        with self._mu:
            self._n += 1
        try:
            yield
        finally:
            with self._mu:
                self._n -= 1
                self._last = time.monotonic()

    def idle_s(self) -> float:
        """Số giây kể từ lần cuối người dùng làm gì (0 nếu đang làm)."""
        with self._mu:
            return 0.0 if self._n else time.monotonic() - self._last


@dataclass
class Components:
    settings: Settings
    db: MetadataStore
    embedder: Embedder
    vectors: QdrantStore
    retriever: Retriever
    pipeline: IngestionPipeline
    # SQLite + Qdrant nhúng không an toàn khi nhiều thread ghi/đọc xen kẽ: API giữ khoá này
    # mỗi lần truy xuất hoặc nạp tài liệu.
    lock: threading.Lock = field(default_factory=threading.Lock)
    activity: UserActivity = field(default_factory=UserActivity)

    def close(self) -> None:
        self.vectors.close()
        self.db.close()


def build_components(
    settings: Settings | None = None, embedder: Embedder | None = None
) -> Components:
    settings = settings or get_settings()
    db = MetadataStore(settings.sqlite_path)
    vectors = QdrantStore(settings)
    embedder = embedder or OpenAICompatEmbedder(settings)
    return Components(
        settings=settings,
        db=db,
        embedder=embedder,
        vectors=vectors,
        retriever=Retriever(settings, db, embedder, vectors),
        pipeline=IngestionPipeline(settings, db, embedder, vectors),
    )
