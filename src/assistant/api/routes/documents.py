"""Quản lý tài liệu qua API: upload (nạp ngay), liệt kê, xoá.

Mọi thao tác đi qua process API nên Qdrant nhúng chỉ bị một process giữ khoá.
Các hàm là `def` (không async) để FastAPI chạy trong threadpool, không chặn event loop
trong lúc parse/embedding.
"""

import re
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from assistant.api.deps import get_components, get_conversations
from assistant.ingestion.parsers import SUPPORTED_SUFFIXES
from assistant.rag.factory import Components
from assistant.services.conversations import ConversationStore

router = APIRouter(prefix="/api/documents", tags=["documents"])

_GROUP = re.compile(r"^[\w-]{1,64}$")


@router.get("")
def list_documents(c: Components = Depends(get_components)):
    with c.lock:
        docs = c.db.list_documents()
        digests = c.db.list_digests()
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "title": d.title,
            "group": d.department,
            "file_type": d.file_type,
            "chunks": d.n_chunks,
            "ingested_at": d.ingested_at,
            # AI đọc trước tài liệu dài: pending | running | done | failed | skip (ngắn, đọc thẳng)
            "digest": {
                "status": g.status,
                "parts_done": g.parts_done,
                "parts_total": g.parts_total,
                "error": g.error,
            }
            if (g := digests.get(d.id))
            else None,
        }
        for d in docs
        if d.department != "news"  # bài báo nằm ở khu Tin tức, không lẫn vào thư viện
    ]


@router.post("")
def upload_document(
    file: UploadFile = File(...),
    group: str = Form("uploads"),
    c: Components = Depends(get_components),
):
    """Lưu file vào UPLOADS_DIR/<group>/ rồi nạp. Upload lại cùng tên = thay bản cũ."""
    name = Path(file.filename or "").name
    if not name or name.startswith("."):
        raise HTTPException(400, "Tên file không hợp lệ")
    if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise HTTPException(400, f"Chỉ hỗ trợ: {', '.join(sorted(SUPPORTED_SUFFIXES))}")
    if not _GROUP.match(group):
        raise HTTPException(400, "group chỉ gồm chữ, số, '_' hoặc '-'")

    root = Path(c.settings.uploads_dir)
    dest = root / group / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    limit = c.settings.max_upload_mb * 1024 * 1024
    size = 0
    tmp = dest.with_name(f".{name}.part")
    with tmp.open("wb") as out:
        while block := file.file.read(1 << 20):
            size += len(block)
            if size > limit:
                out.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, f"File lớn hơn {c.settings.max_upload_mb} MB")
            out.write(block)
    tmp.replace(dest)

    with c.activity.active():  # việc nền của tin tức nhường máy cho file của người dùng
        result = c.pipeline.ingest_file(dest, root, department=group, lock=c.lock)
    if result.status == "failed":
        raise HTTPException(422, f"Không nạp được {name}: {result.error}")
    return asdict(result)


@router.delete("/{document_id}")
def delete_document(
    document_id: str,
    c: Components = Depends(get_components),
    conversations: ConversationStore = Depends(get_conversations),
):
    with c.lock:
        doc = c.db.get_document_by_id(document_id)
        if doc is None:
            raise HTTPException(404, "Không có tài liệu này")
        c.vectors.delete_document(document_id)
        c.db.delete_document(document_id)
    conversations.forget_document(document_id)
    # Chỉ xoá file gốc nếu nó là file upload (nằm trong UPLOADS_DIR)
    root = Path(c.settings.uploads_dir).resolve()
    path = (root / doc.source_path).resolve()
    if path.is_file() and path.is_relative_to(root):
        path.unlink()
    return {"deleted": document_id, "filename": doc.filename}
