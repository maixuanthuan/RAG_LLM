import hashlib
import logging
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from assistant.core.config import Settings
from assistant.ingestion.chunking import chunk_blocks
from assistant.ingestion.models import ParseError
from assistant.ingestion.parsers import SUPPORTED_SUFFIXES, parse_file
from assistant.rag.db import ChunkRow, DocumentRecord, MetadataStore
from assistant.rag.embedder import Embedder, EmbeddingError
from assistant.rag.vectors import QdrantStore, VectorStoreError

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    path: str
    status: str  # ingested | replaced | skipped | empty | failed
    document_id: str | None = None
    chunks: int = 0
    pages: int | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def infer_department(rel_path: Path) -> str:
    """Thư mục con đầu tiên dưới thư mục gốc = phòng ban; file nằm ở gốc = 'general'."""
    return rel_path.parts[0] if len(rel_path.parts) > 1 else "general"


class IngestionPipeline:
    def __init__(
        self, settings: Settings, db: MetadataStore, embedder: Embedder, vectors: QdrantStore
    ) -> None:
        self._s = settings
        self._db = db
        self._embedder = embedder
        self._vectors = vectors

    def ingest_path(
        self, path: Path, department: str | None = None, force: bool = False
    ) -> list[IngestResult]:
        path = path.resolve()
        if path.is_file():
            return [self.ingest_file(path, path.parent, department, force)]
        files = sorted(
            p
            for p in path.rglob("*")
            if p.is_file()
            and p.suffix.lower() in SUPPORTED_SUFFIXES
            and not p.name.startswith(("~$", "."))
        )
        return [self.ingest_file(p, path, department, force) for p in files]

    def ingest_file(
        self,
        path: Path,
        root: Path,
        department: str | None = None,
        force: bool = False,
        lock=None,
    ) -> IngestResult:
        """Nạp một file. `lock` (tuỳ chọn): khoá CSDL dùng chung với API; chỉ giữ khoá lúc đọc /
        ghi CSDL, còn phần chậm (đọc file, embedding) chạy ngoài khoá để chat không phải chờ."""
        guard = lock or nullcontext()
        rel = path.relative_to(root)
        source_path = rel.as_posix()
        document_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"doc:{source_path}"))
        result = IngestResult(path=source_path, status="failed", document_id=document_id)
        try:
            sha = sha256_file(path)
            with guard:
                existing = self._db.get_document(source_path)
            if existing and existing.sha256 == sha and not force:
                result.status = "skipped"
                result.chunks = existing.n_chunks
                return result

            t0 = time.perf_counter()
            parsed = parse_file(path)
            result.pages = parsed.pages
            result.warnings = list(parsed.warnings)
            drafts = chunk_blocks(parsed.blocks, self._s.chunk_size, self._s.chunk_overlap)

            dept = department or infer_department(rel)

            t_parse = time.perf_counter() - t0
            vectors: list[list[float]] = []
            t1 = time.perf_counter()
            if drafts:
                texts = []
                for d in drafts:
                    header = parsed.title + (f" - {d.section}" if d.section else "")
                    texts.append(f"{header}\n{d.text}")
                vectors = self._embedder.embed_passages(texts)  # embed trước khi xoá bản cũ
            t_embed = time.perf_counter() - t1

            with guard:
                if drafts:
                    self._check_embedding_space(len(vectors[0]))
                existing = self._db.get_document(source_path)  # có thể đổi trong lúc embedding
                self._write(document_id, source_path, path, sha, dept, parsed, drafts, vectors,
                            existing)  # fmt: skip
            total_chars = sum(len(d.text) for d in drafts)
            log.info(
                "nạp %s: %d đoạn, %d ký tự | đọc file %.1fs · embedding %.1fs (%.2fs/đoạn)",
                source_path, len(drafts), total_chars, t_parse, t_embed,
                t_embed / max(len(drafts), 1),
            )  # fmt: skip
            result.chunks = len(drafts)
            old = existing is not None
            result.status = "empty" if not drafts else ("replaced" if old else "ingested")
            if not drafts:
                result.warnings.append("Không trích được chữ nào từ file này")
        except (ParseError, EmbeddingError, VectorStoreError) as e:
            result.error = str(e)
        except Exception as e:  # lỗi bất ngờ của một file không được làm hỏng cả đợt nạp
            log.exception("Lỗi nạp %s", source_path)
            result.error = f"{type(e).__name__}: {e}"
        return result

    def _write(self, document_id, source_path, path, sha, dept, parsed, drafts, vectors, existing):
        """Ghi tài liệu đã embedding vào CSDL + kho vector (gọi khi đang giữ khoá)."""
        if existing is not None:
            self._vectors.delete_document(document_id)
            self._db.delete_document_content(document_id)

        record = DocumentRecord(
            id=document_id,
            source_path=source_path,
            filename=path.name,
            title=parsed.title,
            sha256=sha,
            department=dept,
            file_type=path.suffix.lower().lstrip("."),
            n_chunks=len(drafts),
            ingested_at=self._db.now(),
        )
        self._db.save_document(record)
        if drafts:
            try:
                rows = [
                    ChunkRow(
                        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}:{d.ord}")),
                        document_id=document_id,
                        ord=d.ord,
                        text=d.text,
                        page=d.page,
                        section=d.section,
                        department=dept,
                    )
                    for d in drafts
                ]
                self._db.add_chunks(rows, filename=path.name, title=parsed.title)
                self._vectors.upsert(
                    [r.id for r in rows],
                    vectors,
                    [
                        {
                            "document_id": document_id,
                            "document": path.name,
                            "department": dept,
                            "page": r.page,
                            "section": r.section,
                            "ord": r.ord,
                        }
                        for r in rows
                    ],
                )
            except Exception:
                # tránh trạng thái nửa vời: xoá hết để lần sau nạp lại từ đầu
                self._vectors.delete_document(document_id)
                self._db.delete_document(document_id)
                raise
        # Tài liệu dài hơn mức đọc nguyên văn được -> xếp hàng cho AI đọc trước, ghi chú
        total_chars = sum(len(d.text) for d in drafts)
        self._db.delete_digest(document_id)
        self._db.set_digest(
            document_id, "pending" if total_chars > self._s.focus_max_chars else "skip"
        )

    def _check_embedding_space(self, dim: int) -> None:
        """Không trộn vector của hai model embedding khác nhau trong cùng một collection."""
        model = self._embedder.model
        stored = self._db.get_meta("embed_model")
        if stored and stored != model and self._vectors.count() > 0:
            raise VectorStoreError(
                f"Dữ liệu đã nạp bằng model '{stored}', hiện đang dùng '{model}'. "
                "Nạp lại từ đầu với --reset."
            )
        self._vectors.ensure_collection(dim)
        self._db.set_meta("embed_model", model)
        self._db.set_meta("embed_dim", str(dim))
