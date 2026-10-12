"""Chuyển vector từ Qdrant nhúng (thư mục data/qdrant) sang Qdrant server (Docker).

Chép thẳng vector + payload, KHÔNG embed lại. Chạy khi API đã tắt (Qdrant nhúng khoá thư mục):

    make qdrant-switch            # tắt hệ thống -> bật Qdrant server -> chép -> sửa .env -> bật lại
    python scripts/qdrant_migrate.py [--url http://localhost:6333] [--force]
"""

import argparse
import re
import sys
from pathlib import Path

from qdrant_client import QdrantClient, models

from assistant.core.config import get_settings


def migrate(src: QdrantClient, dst: QdrantClient, collection: str, batch: int = 256) -> int:
    """Chép toàn bộ điểm của collection từ src sang dst (dst phải chưa có hoặc rỗng)."""
    size = src.get_collection(collection).config.params.vectors.size
    if not dst.collection_exists(collection):
        dst.create_collection(
            collection,
            vectors_config=models.VectorParams(size=size, distance=models.Distance.COSINE),
        )
    copied, offset = 0, None
    while True:
        points, offset = src.scroll(
            collection, limit=batch, offset=offset, with_vectors=True, with_payload=True
        )
        if points:
            dst.upsert(
                collection,
                points=[
                    models.PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in points
                ],
            )
            copied += len(points)
            print(f"  đã chép {copied} vector", end="\r", flush=True)
        if offset is None:
            break
    print()
    return copied


def set_env(path: Path, key: str, value: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    line = f"{key}={value}"
    if re.search(rf"^{key}=.*$", text, flags=re.M):
        text = re.sub(rf"^{key}=.*$", line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:6333")
    p.add_argument("--force", action="store_true", help="ghi đè dù server đã có dữ liệu")
    args = p.parse_args()
    s = get_settings()
    if s.qdrant_url:
        print(f"Đang dùng Qdrant server rồi (QDRANT_URL={s.qdrant_url}), không cần chuyển.")
        return 0
    dst = QdrantClient(url=args.url)
    try:
        dst.get_collections()
    except Exception as e:
        print(f"LỖI: không kết nối được Qdrant server {args.url} ({e}). Chạy: make qdrant-up")
        return 1
    try:
        src = QdrantClient(path=s.qdrant_path)
    except Exception as e:
        print(f"LỖI: không mở được {s.qdrant_path} ({e}). API còn chạy? Chạy: make dev-down")
        return 1
    col = s.qdrant_collection
    if src.collection_exists(col):
        if dst.collection_exists(col) and dst.count(col).count and not args.force:
            print(f"LỖI: server đã có {dst.count(col).count} vector. Thêm --force để ghi đè.")
            return 1
        n = migrate(src, dst, col)
        for field in ("department", "document_id"):
            dst.create_payload_index(col, field_name=field,
                                     field_schema=models.PayloadSchemaType.KEYWORD)  # fmt: skip
        total = dst.count(col, exact=True).count
        print(f"Đã chép {n} vector, server hiện có {total}.")
        if total < n:
            print("LỖI: số vector trên server ít hơn nguồn, không đổi cấu hình.")
            return 1
    else:
        print("Kho nhúng chưa có dữ liệu, chỉ chuyển cấu hình.")
    src.close()
    set_env(Path(".env"), "QDRANT_URL", args.url)
    print(f"Đã đặt QDRANT_URL={args.url} trong .env.")
    print(f"Thư mục {s.qdrant_path} giữ nguyên làm bản sao lưu.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
