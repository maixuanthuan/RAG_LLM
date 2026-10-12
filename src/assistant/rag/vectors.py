"""Qdrant: lưu vector. Chạy nhúng (thư mục local) hoặc kết nối server qua QDRANT_URL."""

from pathlib import Path

from qdrant_client import QdrantClient, models

from assistant.core.config import Settings


class VectorStoreError(Exception):
    pass


class QdrantStore:
    def __init__(self, settings: Settings) -> None:
        if settings.qdrant_url:
            self.client = QdrantClient(url=settings.qdrant_url)
        else:
            Path(settings.qdrant_path).mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=settings.qdrant_path)
        self.collection = settings.qdrant_collection
        self.is_server = bool(settings.qdrant_url)

    def close(self) -> None:
        self.client.close()

    def has_collection(self) -> bool:
        return self.client.collection_exists(self.collection)

    def ensure_collection(self, dim: int) -> None:
        if not self.has_collection():
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
            )
            self.ensure_indexes()
            return
        existing = self.client.get_collection(self.collection).config.params.vectors.size
        if existing != dim:
            raise VectorStoreError(
                f"Collection '{self.collection}' có {existing} chiều nhưng embedding hiện tại "
                f"có {dim} chiều. Nạp lại từ đầu với --reset."
            )

    def ensure_indexes(self) -> None:
        """Qdrant server: chỉ mục cho các trường hay lọc (khu tin tức / tài liệu đính kèm)."""
        if not self.is_server:
            return  # chế độ nhúng không dùng chỉ mục
        for field in ("department", "document_id"):
            self.client.create_payload_index(
                self.collection, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD
            )

    def upsert(self, ids: list[str], vectors: list[list[float]], payloads: list[dict]) -> None:
        self.client.upsert(
            collection_name=self.collection,
            points=[
                models.PointStruct(id=i, vector=v, payload=p)
                for i, v, p in zip(ids, vectors, payloads, strict=True)
            ],
        )

    def delete_document(self, document_id: str) -> None:
        if not self.has_collection():
            return
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id", match=models.MatchValue(value=document_id)
                        )
                    ]
                )
            ),
        )

    def search(
        self,
        vector: list[float],
        limit: int,
        departments: list[str] | None = None,
        document_ids: list[str] | None = None,
        exclude_departments: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        if not self.has_collection():
            return []
        must = []
        must_not = []
        if exclude_departments:
            must_not.append(
                models.FieldCondition(
                    key="department", match=models.MatchAny(any=exclude_departments)
                )
            )
        if departments:
            must.append(
                models.FieldCondition(key="department", match=models.MatchAny(any=departments))
            )
        if document_ids:
            must.append(
                models.FieldCondition(key="document_id", match=models.MatchAny(any=document_ids))
            )
        flt = models.Filter(must=must, must_not=must_not) if must or must_not else None
        result = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=limit,
            query_filter=flt,
            with_payload=False,
        )
        return [(str(p.id), float(p.score)) for p in result.points]

    def count(self) -> int:
        if not self.has_collection():
            return 0
        return self.client.count(collection_name=self.collection, exact=True).count

    def reset(self) -> None:
        if self.has_collection():
            self.client.delete_collection(self.collection)
