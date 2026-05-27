from __future__ import annotations

import logging
from pathlib import Path

import pyarrow as pa

from offline_rag.chunker import Chunk

logger = logging.getLogger(__name__)

TABLE_NAME = "chunks"


def _schema(embedding_dim: int) -> pa.Schema:
    return pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("file_path", pa.string()),
            pa.field("file_hash", pa.string()),
            pa.field("chunk_idx", pa.int32()),
            pa.field("file_type", pa.string()),
            pa.field("mtime", pa.float64()),
            pa.field("parent_heading", pa.string()),
            pa.field("lang", pa.string()),
            pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), embedding_dim)),
        ]
    )


def _to_dict(chunk: Chunk, dim: int) -> dict:
    vec = chunk.vector or [0.0] * dim
    return {
        "id": chunk.id,
        "file_path": chunk.file_path,
        "file_hash": chunk.file_hash,
        "chunk_idx": chunk.chunk_idx,
        "file_type": chunk.file_type,
        "mtime": chunk.mtime,
        "parent_heading": chunk.parent_heading,
        "lang": chunk.lang,
        "text": chunk.text,
        "vector": [float(x) for x in vec],
    }


class VectorStore:
    def __init__(self, db_path: str | Path, embedding_dim: int = 768) -> None:
        import lancedb

        self._dim = embedding_dim
        self._db_path = Path(db_path)
        self._db_path.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self._db_path))
        self._table = self._open_or_create()
        self._fts_dirty = False

    def _open_or_create(self):
        import lancedb

        if TABLE_NAME in self._db.table_names():
            return self._db.open_table(TABLE_NAME)
        return self._db.create_table(TABLE_NAME, schema=_schema(self._dim))

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        data = [_to_dict(c, self._dim) for c in chunks]
        self._table.add(data)
        self._fts_dirty = True

    def delete_by_file_hash(self, file_hash: str) -> None:
        try:
            self._table.delete(f"file_hash = '{file_hash}'")
            self._fts_dirty = True
        except Exception as e:
            logger.debug(f"delete_by_file_hash({file_hash}): {e}")

    def ensure_fts_index(self) -> None:
        """Build/rebuild FTS index. Call after batch ingestion."""
        try:
            self._table.create_fts_index("text", replace=True)
            self._fts_dirty = False
            logger.info("FTS index rebuilt")
        except Exception as e:
            logger.warning(f"FTS index creation failed: {e}")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def vector_search(self, query_vector: list[float], k: int = 20) -> list[dict]:
        try:
            return (
                self._table.search(query_vector)
                .metric("cosine")
                .limit(k)
                .to_list()
            )
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return []

    def fts_search(self, query: str, k: int = 20) -> list[dict]:
        try:
            return (
                self._table.search(query, query_type="fts")
                .limit(k)
                .to_list()
            )
        except Exception as e:
            logger.debug(f"FTS search failed (index may not exist yet): {e}")
            return []

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        try:
            total = self._table.count_rows()
            df = self._table.to_pandas(columns=["file_path"])
            unique = int(df["file_path"].nunique())
            return {"total_chunks": total, "unique_files": unique}
        except Exception:
            return {"total_chunks": 0, "unique_files": 0}
