"""Chuyển vector từ Qdrant nhúng sang server: chép đủ vector + payload, tìm kiếm vẫn đúng."""

import importlib.util
from pathlib import Path

from qdrant_client import QdrantClient

from assistant.devtools.sample_docs import make_sample_docs

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("qmig", ROOT / "scripts" / "qdrant_migrate.py")
qmig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qmig)


def test_migrate_copies_all_points_and_payloads(components, tmp_path):
    make_sample_docs(tmp_path / "docs")
    components.pipeline.ingest_path(tmp_path / "docs")
    src = components.vectors.client
    col = components.settings.qdrant_collection
    dst = QdrantClient(path=str(tmp_path / "server"))  # thay cho server thật trong test
    n = qmig.migrate(src, dst, col, batch=4)
    assert n == components.vectors.count() == dst.count(col, exact=True).count > 4
    p = src.scroll(col, limit=1, with_vectors=True, with_payload=True)[0][0]
    q = dst.retrieve(col, [p.id], with_vectors=True, with_payload=True)[0]
    assert q.payload == p.payload and q.vector == p.vector
    dst.close()


def test_set_env_replaces_or_appends(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\nQDRANT_URL=\nB=2\n", encoding="utf-8")
    qmig.set_env(env, "QDRANT_URL", "http://localhost:6333")
    assert env.read_text() == "A=1\nQDRANT_URL=http://localhost:6333\nB=2\n"
    env.write_text("A=1", encoding="utf-8")
    qmig.set_env(env, "QDRANT_URL", "http://x")
    assert env.read_text() == "A=1\nQDRANT_URL=http://x\n"
