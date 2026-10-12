"""Lưu lịch sử hội thoại trong SQLite để giao diện web liệt kê và mở lại sau khi tải lại trang."""

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

NEW_TITLE = "Cuộc trò chuyện mới"

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations(
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
"""


@dataclass
class Conversation:
    id: str
    title: str
    source_ids: list[str]  # tài liệu đính kèm: hỏi trong cuộc này = hỏi về các tài liệu này
    created_at: str
    updated_at: str
    kind: str = "chat"  # chat (tài liệu, làm việc) | news (khu Tin tức)


@dataclass
class Message:
    id: int
    role: str
    content: str
    created_at: str
    meta: dict = field(default_factory=dict)  # nguồn, cảnh báo, debug, thống kê


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConversationStore:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(conversations)")}
        if "kind" not in cols:  # nâng cấp DB tạo từ bản trước
            self._conn.execute(
                "ALTER TABLE conversations ADD COLUMN kind TEXT NOT NULL DEFAULT 'chat'"
            )
            self._conn.commit()
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _conv(row) -> Conversation:
        d = dict(row)
        d["source_ids"] = json.loads(d["source_ids"])
        return Conversation(**d)

    def create(
        self, title: str = NEW_TITLE, source_ids: list[str] | None = None, kind: str = "chat"
    ) -> Conversation:
        now = _now()
        conv = Conversation(str(uuid.uuid4()), title, list(source_ids or []), now, now, kind)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO conversations(id, title, source_ids, created_at, updated_at, kind) "
                "VALUES(?,?,?,?,?,?)",
                (conv.id, conv.title, json.dumps(conv.source_ids), now, now, kind),
            )
        return conv

    def list_all(self, kind: str | None = "chat") -> list[Conversation]:
        """kind=None: mọi loại (dùng nội bộ); mặc định chỉ hội thoại khu chat."""
        sql, params = "SELECT * FROM conversations", []
        if kind:
            sql += " WHERE kind=?"
            params.append(kind)
        with self._lock:
            rows = self._conn.execute(
                sql + " ORDER BY updated_at DESC, created_at DESC", params
            ).fetchall()
        return [self._conv(r) for r in rows]

    def get(self, cid: str) -> Conversation | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return self._conv(row) if row else None

    def update(
        self, cid: str, title: str | None = None, source_ids: list[str] | None = None
    ) -> Conversation | None:
        with self._lock, self._conn:
            if title is not None:
                self._conn.execute("UPDATE conversations SET title=? WHERE id=?", (title, cid))
            if source_ids is not None:
                self._conn.execute(
                    "UPDATE conversations SET source_ids=? WHERE id=?",
                    (json.dumps(list(dict.fromkeys(source_ids))), cid),
                )
        return self.get(cid)

    def delete(self, cid: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
        return cur.rowcount > 0

    def messages(self, cid: str) -> list[Message]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, role, content, created_at, meta FROM messages "
                "WHERE conversation_id=? ORDER BY id",
                (cid,),
            ).fetchall()
        return [Message(**{**dict(r), "meta": json.loads(r["meta"])}) for r in rows]

    def add_message(self, cid: str, role: str, content: str, meta: dict | None = None) -> None:
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO messages(conversation_id, role, content, meta, created_at) "
                "VALUES(?,?,?,?,?)",
                (cid, role, content, json.dumps(meta or {}, ensure_ascii=False), now),
            )
            self._conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))

    def forget_document(self, document_id: str) -> None:
        """Tài liệu bị xoá khỏi kho thì gỡ khỏi mọi cuộc trò chuyện đang đính kèm nó."""
        for conv in self.list_all(kind=None):
            if document_id in conv.source_ids:
                self.update(conv.id, source_ids=[s for s in conv.source_ids if s != document_id])
