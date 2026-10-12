"""SQLite: metadata tài liệu, nội dung chunk và chỉ mục từ khoá BM25 (FTS5)."""

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from assistant.ingestion.text import fold

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(
    id TEXT PRIMARY KEY,
    source_path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    title TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    department TEXT NOT NULL,
    file_type TEXT NOT NULL,
    n_chunks INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks(
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    text TEXT NOT NULL,
    page INTEGER,
    section TEXT,
    department TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(search_text, tokenize='unicode61');
-- "Đọc trước" tài liệu dài: trạng thái và ghi chú theo từng phần (level 1), các bản gộp
-- (level 2, 3, ...) và tổng quan (level 0). Nội dung gốc vẫn nằm nguyên trong bảng chunks.
CREATE TABLE IF NOT EXISTS doc_digest(
    document_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,  -- pending | running | done | failed | skip (tài liệu ngắn)
    parts_total INTEGER NOT NULL DEFAULT 0,
    parts_done INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    updated_at TEXT NOT NULL
);
-- Tin tức: mỗi bài báo là một tài liệu (nhóm "news") kèm thông tin bài gốc.
CREATE TABLE IF NOT EXISTS articles(
    document_id TEXT PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    lead TEXT,
    published_at TEXT NOT NULL,
    summary TEXT,
    summary_status TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed
    fetched_at TEXT NOT NULL,
    tags TEXT,  -- chuyên mục theo từ khoá, dạng ",thoi-tiet,chinh-tri,"
    event_id TEXT,  -- các bài cùng một sự kiện (nhiều báo cùng đưa) có chung event_id
    title_vec BLOB  -- vector tiêu đề + sapo (float16) để gom bài cùng sự kiện
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE TABLE IF NOT EXISTS doc_notes(
    document_id TEXT NOT NULL,
    level INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    page_from INTEGER,
    page_to INTEGER,
    text TEXT NOT NULL,
    PRIMARY KEY (document_id, level, idx)
);
"""

# Từ chức năng tiếng Việt (đã bỏ dấu) làm nhiễu khi khớp từ khoá
_STOPWORDS = {
    "la", "va", "cua", "co", "khong", "the", "nao", "bao", "nhieu", "duoc", "cho", "thi",
    "trong", "khi", "de", "se", "cac", "mot", "nay", "gi", "sao", "ve", "voi", "nhung",
    "hay", "hoac", "o", "ai", "do", "bi", "den", "tu", "tai", "nhu", "phai", "can",
}  # fmt: skip


@dataclass
class ChunkRow:
    id: str
    document_id: str
    ord: int
    text: str
    page: int | None
    section: str | None
    department: str


@dataclass
class ChunkRecord:
    id: str
    document_id: str
    document: str  # tên file
    title: str
    ord: int
    text: str
    page: int | None
    section: str | None
    department: str


@dataclass
class DigestState:
    document_id: str
    status: str
    parts_total: int
    parts_done: int
    error: str | None
    updated_at: str


@dataclass
class NoteRecord:
    level: int
    idx: int
    page_from: int | None
    page_to: int | None
    text: str


@dataclass
class ArticleRecord:
    document_id: str
    url: str
    source: str
    category: str
    title: str
    lead: str | None
    published_at: str  # ISO 8601 có múi giờ
    summary: str | None
    summary_status: str
    fetched_at: str
    tags: str | None = None
    event_id: str | None = None

    def in_category(self, cat: str) -> bool:
        return self.category == cat or f",{cat}," in (self.tags or "")


ART_COLS = (
    "document_id, url, source, category, title, lead, published_at, summary, summary_status, "
    "fetched_at, tags, event_id"
)


@dataclass
class DocumentRecord:
    id: str
    source_path: str
    filename: str
    title: str
    sha256: str
    department: str
    file_type: str
    n_chunks: int
    ingested_at: str


def build_fts_query(query: str) -> str | None:
    """Câu hỏi -> truy vấn FTS5: các từ (OR) cộng thêm cụm 2 từ liền kề để ưu tiên đúng cụm."""
    tokens = [t for t in re.findall(r"\w+", fold(query)) if t not in _STOPWORDS]
    if not tokens:
        return None
    terms = [f'"{t}"' for t in dict.fromkeys(tokens)]
    terms += [f'"{a} {b}"' for a, b in zip(tokens, tokens[1:], strict=False)]
    return " OR ".join(terms)


class MetadataStore:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # API chạy truy xuất trong threadpool nên kết nối dùng chung giữa các thread;
        # việc tuần tự hoá do Components.lock đảm nhiệm.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(articles)")}
        for col, typ in (("tags", "TEXT"), ("event_id", "TEXT"), ("title_vec", "BLOB")):
            if col not in cols:  # nâng cấp DB tạo từ bản trước
                self.conn.execute(f"ALTER TABLE articles ADD COLUMN {col} {typ}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- meta ----
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ---- documents ----
    def get_document(self, source_path: str) -> DocumentRecord | None:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE source_path=?", (source_path,)
        ).fetchone()
        return DocumentRecord(**dict(row)) if row else None

    def get_document_by_id(self, document_id: str) -> DocumentRecord | None:
        row = self.conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        return DocumentRecord(**dict(row)) if row else None

    def count_documents(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]

    def count_chunks(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]

    def list_documents(self) -> list[DocumentRecord]:
        rows = self.conn.execute("SELECT * FROM documents ORDER BY department, filename").fetchall()
        return [DocumentRecord(**dict(r)) for r in rows]

    def save_document(self, doc: DocumentRecord) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO documents(id, source_path, filename, title, sha256, "
                "department, file_type, n_chunks, ingested_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    doc.id, doc.source_path, doc.filename, doc.title, doc.sha256,
                    doc.department, doc.file_type, doc.n_chunks, doc.ingested_at,
                ),
            )  # fmt: skip

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def delete_document_content(self, document_id: str) -> list[str]:
        """Xoá chunk (và chỉ mục từ khoá) của tài liệu, trả về id các chunk đã xoá."""
        rows = self.conn.execute(
            "SELECT rowid, id FROM chunks WHERE document_id=?", (document_id,)
        ).fetchall()
        with self.conn:
            self.conn.executemany(
                "DELETE FROM chunks_fts WHERE rowid=?", [(r["rowid"],) for r in rows]
            )
            self.conn.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
        return [r["id"] for r in rows]

    def delete_document(self, document_id: str) -> list[str]:
        ids = self.delete_document_content(document_id)
        self.delete_digest(document_id)
        with self.conn:
            self.conn.execute("DELETE FROM articles WHERE document_id=?", (document_id,))
        with self.conn:
            self.conn.execute("DELETE FROM documents WHERE id=?", (document_id,))
        return ids

    # ---- tin tức ----
    def article_urls(self) -> set[str]:
        return {r["url"] for r in self.conn.execute("SELECT url FROM articles").fetchall()}

    def save_article(self, a: ArticleRecord, title_vec: bytes | None = None) -> None:
        with self.conn:
            self.conn.execute(
                f"INSERT OR REPLACE INTO articles({ART_COLS}, title_vec) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (a.document_id, a.url, a.source, a.category, a.title, a.lead, a.published_at,
                 a.summary, a.summary_status, a.fetched_at, a.tags, a.event_id, title_vec),
            )  # fmt: skip

    def update_article(self, document_id: str, **fields) -> None:
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            with self.conn:
                self.conn.execute(
                    f"UPDATE articles SET {sets} WHERE document_id=?",
                    [*fields.values(), document_id],
                )

    def article_vectors(self, since: str, until: str) -> list[tuple[str, str, bytes]]:
        """(document_id, event_id, title_vec) của các bài đăng trong khoảng thời gian."""
        rows = self.conn.execute(
            "SELECT document_id, event_id, title_vec FROM articles WHERE title_vec IS NOT NULL "
            "AND published_at >= ? AND published_at <= ?",
            (since, until),
        ).fetchall()
        return [(r["document_id"], r["event_id"], r["title_vec"]) for r in rows]

    def articles_missing(self, column: str, limit: int = 50) -> list[ArticleRecord]:
        assert column in ("tags", "title_vec")
        rows = self.conn.execute(
            f"SELECT {ART_COLS} FROM articles WHERE {column} IS NULL ORDER BY published_at DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [ArticleRecord(**dict(r)) for r in rows]

    def get_articles(self, document_ids: list[str]) -> dict[str, ArticleRecord]:
        if not document_ids:
            return {}
        marks = ",".join("?" * len(document_ids))
        rows = self.conn.execute(
            f"SELECT {ART_COLS} FROM articles WHERE document_id IN ({marks})", document_ids
        ).fetchall()
        return {r["document_id"]: ArticleRecord(**dict(r)) for r in rows}

    def list_articles(
        self, since: str | None = None, category: str | None = None, limit: int = 200
    ) -> list[ArticleRecord]:
        sql, params = f"SELECT {ART_COLS} FROM articles WHERE 1=1", []
        if since:
            sql += " AND published_at >= ?"
            params.append(since)
        if category:  # chuyên mục gốc hoặc chuyên mục theo từ khoá
            sql += " AND (category = ? OR tags LIKE ?)"
            params += [category, f"%,{category},%"]
        sql += " ORDER BY published_at DESC LIMIT ?"
        params.append(limit)
        return [ArticleRecord(**dict(r)) for r in self.conn.execute(sql, params).fetchall()]

    def article_stats(self) -> tuple[int, int, Counter]:
        """(tổng số bài, số bài chờ tóm tắt, số bài theo chuyên mục + nhãn chủ đề), đếm bằng SQL."""
        total, pending = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(summary_status='pending'), 0) FROM articles"
        ).fetchone()
        counts: Counter = Counter()
        for cat, n in self.conn.execute("SELECT category, COUNT(*) FROM articles GROUP BY 1"):
            counts[cat] += n
        for tags, n in self.conn.execute(
            "SELECT tags, COUNT(*) FROM articles WHERE tags IS NOT NULL AND tags != '' GROUP BY 1"
        ):
            for t in tags.split(","):
                if t:
                    counts[t] += n
        return total, pending, counts

    def next_article_to_summarize(self) -> ArticleRecord | None:
        row = self.conn.execute(
            f"SELECT {ART_COLS} FROM articles WHERE summary_status='pending' "
            "ORDER BY published_at DESC LIMIT 1"
        ).fetchone()
        return ArticleRecord(**dict(row)) if row else None

    def set_article_summary(self, document_id: str, summary: str | None, status: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE articles SET summary=?, summary_status=? WHERE document_id=?",
                (summary, status, document_id),
            )

    def articles_before(self, published_before: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT document_id FROM articles WHERE published_at < ?", (published_before,)
        ).fetchall()
        return [r["document_id"] for r in rows]

    # ---- đọc trước (digest) ----
    def set_digest(
        self,
        document_id: str,
        status: str,
        parts_total: int | None = None,
        parts_done: int | None = None,
        error: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO doc_digest(document_id, status, parts_total, parts_done, error, "
                "updated_at) VALUES(?, ?, COALESCE(?, 0), COALESCE(?, 0), ?, ?) "
                "ON CONFLICT(document_id) DO UPDATE SET status=excluded.status, "
                "parts_total=COALESCE(?, parts_total), parts_done=COALESCE(?, parts_done), "
                "error=excluded.error, updated_at=excluded.updated_at",
                (document_id, status, parts_total, parts_done, error, self.now(),
                 parts_total, parts_done),
            )  # fmt: skip

    def get_digest(self, document_id: str) -> DigestState | None:
        row = self.conn.execute(
            "SELECT * FROM doc_digest WHERE document_id=?", (document_id,)
        ).fetchone()
        return DigestState(**dict(row)) if row else None

    def list_digests(self) -> dict[str, DigestState]:
        rows = self.conn.execute("SELECT * FROM doc_digest").fetchall()
        return {r["document_id"]: DigestState(**dict(r)) for r in rows}

    def next_pending_digest(self) -> str | None:
        row = self.conn.execute(
            "SELECT document_id FROM doc_digest WHERE status='pending' ORDER BY updated_at LIMIT 1"
        ).fetchone()
        return row["document_id"] if row else None

    def documents_without_digest(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM documents WHERE id NOT IN (SELECT document_id FROM doc_digest)"
        ).fetchall()
        return [r["id"] for r in rows]

    def prioritize_digests(self, document_ids: list[str]) -> None:
        """Đưa các tài liệu đang được hỏi lên đầu hàng đợi đọc trước (sắp theo updated_at)."""
        with self.conn:
            self.conn.executemany(
                "UPDATE doc_digest SET updated_at='0' WHERE document_id=? AND status='pending'",
                [(d,) for d in document_ids],
            )

    def reset_running_digests(self) -> None:
        """Process trước bị tắt giữa chừng: đọc lại từ đầu."""
        with self.conn:
            self.conn.execute("UPDATE doc_digest SET status='pending' WHERE status='running'")

    def save_note(
        self, document_id: str, level: int, idx: int, page_from, page_to, text: str
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO doc_notes(document_id, level, idx, page_from, page_to, "
                "text) VALUES(?,?,?,?,?,?)",
                (document_id, level, idx, page_from, page_to, text),
            )

    def get_notes(self, document_id: str, level: int) -> list[NoteRecord]:
        rows = self.conn.execute(
            "SELECT level, idx, page_from, page_to, text FROM doc_notes "
            "WHERE document_id=? AND level=? ORDER BY idx",
            (document_id, level),
        ).fetchall()
        return [NoteRecord(**dict(r)) for r in rows]

    def note_levels(self, document_id: str) -> list[int]:
        rows = self.conn.execute(
            "SELECT DISTINCT level FROM doc_notes WHERE document_id=? ORDER BY level",
            (document_id,),
        ).fetchall()
        return [r["level"] for r in rows]

    def delete_digest(self, document_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM doc_notes WHERE document_id=?", (document_id,))
            self.conn.execute("DELETE FROM doc_digest WHERE document_id=?", (document_id,))

    # ---- chunks ----
    def add_chunks(self, rows: list[ChunkRow], filename: str, title: str) -> None:
        with self.conn:
            for r in rows:
                cur = self.conn.execute(
                    "INSERT INTO chunks(id, document_id, ord, text, page, section, department) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (r.id, r.document_id, r.ord, r.text, r.page, r.section, r.department),
                )
                search_text = fold(f"{filename} {title} {r.section or ''} {r.text}")
                self.conn.execute(
                    "INSERT INTO chunks_fts(rowid, search_text) VALUES(?, ?)",
                    (cur.lastrowid, search_text),
                )

    def get_chunks(self, ids: list[str]) -> dict[str, ChunkRecord]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            "SELECT c.id, c.document_id, d.filename AS document, d.title, c.ord, c.text, "
            "c.page, c.section, c.department FROM chunks c "
            f"JOIN documents d ON d.id = c.document_id WHERE c.id IN ({marks})",
            ids,
        ).fetchall()
        return {r["id"]: ChunkRecord(**dict(r)) for r in rows}

    def get_document_chunks(self, document_id: str) -> list[ChunkRecord]:
        """Toàn bộ chunk của một tài liệu theo thứ tự gốc (để dựng lại toàn văn)."""
        rows = self.conn.execute(
            "SELECT c.id, c.document_id, d.filename AS document, d.title, c.ord, c.text, "
            "c.page, c.section, c.department FROM chunks c "
            "JOIN documents d ON d.id = c.document_id WHERE c.document_id=? ORDER BY c.ord",
            (document_id,),
        ).fetchall()
        return [ChunkRecord(**dict(r)) for r in rows]

    def keyword_search(
        self,
        query: str,
        limit: int,
        departments: list[str] | None = None,
        document_ids: list[str] | None = None,
        exclude_departments: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        fts_query = build_fts_query(query)
        if fts_query is None:
            return []
        sql = (
            "SELECT c.id AS id, bm25(chunks_fts) AS score FROM chunks_fts "
            "JOIN chunks c ON c.rowid = chunks_fts.rowid WHERE chunks_fts MATCH ?"
        )
        params: list = [fts_query]
        if departments:
            sql += f" AND c.department IN ({','.join('?' * len(departments))})"
            params += departments
        if document_ids:
            sql += f" AND c.document_id IN ({','.join('?' * len(document_ids))})"
            params += document_ids
        if exclude_departments:
            sql += f" AND c.department NOT IN ({','.join('?' * len(exclude_departments))})"
            params += exclude_departments
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [(r["id"], -r["score"]) for r in rows]  # bm25 của SQLite: nhỏ hơn = tốt hơn

    # ---- tổng quan / reset ----
    def stats(self) -> dict:
        docs = self.conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
        chunks = self.conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        by_dep = self.conn.execute(
            "SELECT department, COUNT(*) AS docs, SUM(n_chunks) AS chunks "
            "FROM documents GROUP BY department ORDER BY department"
        ).fetchall()
        return {
            "documents": docs,
            "chunks": chunks,
            "by_department": [dict(r) for r in by_dep],
            "embed_model": self.get_meta("embed_model"),
            "embed_dim": self.get_meta("embed_dim"),
        }

    def reset(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM chunks_fts")
            self.conn.execute("DELETE FROM chunks")
            self.conn.execute("DELETE FROM documents")
            self.conn.execute("DELETE FROM doc_notes")
            self.conn.execute("DELETE FROM articles")
            self.conn.execute("DELETE FROM doc_digest")
            self.conn.execute("DELETE FROM meta")
