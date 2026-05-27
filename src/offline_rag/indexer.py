from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import aiosqlite
import pathspec
from tqdm import tqdm

from offline_rag.chunker import make_chunks
from offline_rag.config import Config
from offline_rag.ollama_client import OllamaClient
from offline_rag.parsers import SUPPORTED_EXTENSIONS, parse_file
from offline_rag.store import VectorStore

logger = logging.getLogger(__name__)

_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".env", "dist", "build", ".idea", ".vscode", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", "htmlcov",
})
_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB

_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS file_state (
    path      TEXT PRIMARY KEY,
    file_hash TEXT NOT NULL,
    mtime     REAL NOT NULL,
    indexed_at REAL NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _load_ragignore(root: Path) -> pathspec.PathSpec | None:
    p = root / ".ragignore"
    if p.exists():
        return pathspec.PathSpec.from_lines("gitwildmatch", p.read_text().splitlines())
    return None


# ---------------------------------------------------------------------------
# FileIndexer
# ---------------------------------------------------------------------------

class FileIndexer:
    def __init__(self, config: Config, store: VectorStore, ollama: OllamaClient) -> None:
        self.config = config
        self.store = store
        self.ollama = ollama

        data_dir = Path(config.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.state_db_path = data_dir / "state.db"

        self._global_ignore = pathspec.PathSpec.from_lines(
            "gitwildmatch", config.ignore_patterns or []
        )

    async def _state_db(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(str(self.state_db_path))
        await conn.execute(_STATE_SCHEMA)
        await conn.commit()
        return conn

    def _should_skip(self, path: Path, root: Path, ragignore: pathspec.PathSpec | None) -> bool:
        # Check skip dirs in any path component
        for part in path.parts:
            if part in _SKIP_DIRS:
                return True

        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        rel_str = rel.as_posix()

        if self._global_ignore.match_file(rel_str):
            return True
        if ragignore and ragignore.match_file(rel_str):
            return True

        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                logger.warning(f"Skipping large file (>50 MB): {path}")
                return True
        except OSError:
            return True

        return False

    def _collect(self, root: Path) -> list[Path]:
        ragignore = _load_ragignore(root)
        files: list[Path] = []
        try:
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if self._should_skip(p, root, ragignore):
                    continue
                if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    # Try plain text for extensionless or unknown files
                    try:
                        p.read_bytes()[:512].decode("utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue
                files.append(p)
        except PermissionError as e:
            logger.warning(f"Permission denied walking {root}: {e}")
        return files

    async def _embed_and_store(
        self,
        chunks_list: list,
        old_hash: str | None,
    ) -> None:
        if not chunks_list:
            return
        texts = [c.text for c in chunks_list]
        embeddings = await self.ollama.embed_batch(
            self.config.embedding_model,
            texts,
            batch_size=self.config.embedding_batch_size,
        )
        for chunk, emb in zip(chunks_list, embeddings):
            chunk.vector = emb

        if old_hash:
            self.store.delete_by_file_hash(old_hash)
        self.store.add_chunks(chunks_list)

    async def index_paths(
        self,
        paths: list[str] | None = None,
        progress_callback=None,
    ) -> dict:
        target_roots = [
            Path(p).expanduser()
            for p in (paths or self.config.paths)
        ]

        async with await self._state_db() as db:
            async with db.execute("SELECT path, file_hash FROM file_state") as cur:
                old_state: dict[str, str] = {row[0]: row[1] async for row in cur}

            all_files: list[Path] = []
            for root in target_roots:
                if root.is_file():
                    all_files.append(root)
                elif root.is_dir():
                    all_files.extend(self._collect(root))

            total_files = len(all_files)
            _report_every = max(1, total_files // 20)
            seen: set[str] = set()
            counts = {"indexed": 0, "skipped": 0, "deleted": 0, "errors": 0}

            pbar = tqdm(all_files, desc="Indexing", unit="file", dynamic_ncols=True)
            for i, file_path in enumerate(pbar):
                path_str = str(file_path)
                seen.add(path_str)
                pbar.set_postfix(f=file_path.name[:35])
                _should_report = progress_callback is not None and (
                    i % _report_every == 0 or i == total_files - 1
                )

                try:
                    mtime = file_path.stat().st_mtime
                    current_hash = _sha256(file_path)

                    if path_str in old_state and old_state[path_str] == current_hash:
                        counts["skipped"] += 1
                        if _should_report:
                            await progress_callback(i + 1, total_files, file_path.name)
                        continue

                    text, meta = parse_file(file_path, self.config)

                    # Vision captioning path (async, handled here)
                    if meta.get("file_type") == "image_vision":
                        try:
                            text = await self.ollama.vision_describe(
                                self.config.vision_model, file_path
                            )
                            meta = {"file_type": "text", "lang": "image"}
                        except Exception as e:
                            logger.warning(f"Vision caption failed for {file_path}: {e}")
                            counts["errors"] += 1
                            if _should_report:
                                await progress_callback(i + 1, total_files, file_path.name)
                            continue

                    if not text or not text.strip():
                        counts["skipped"] += 1
                        if _should_report:
                            await progress_callback(i + 1, total_files, file_path.name)
                        continue

                    # Determine relative path
                    rel_path = path_str
                    for root in target_roots:
                        try:
                            rel_path = file_path.relative_to(root).as_posix()
                            break
                        except ValueError:
                            pass

                    chunks = make_chunks(
                        text=text,
                        file_path=rel_path,
                        file_hash=current_hash,
                        mtime=mtime,
                        file_type=meta.get("file_type", "text"),
                        lang=meta.get("lang", "text"),
                        config_size=self.config.chunk_size_tokens,
                        config_overlap=self.config.chunk_overlap_tokens,
                    )

                    old_hash = old_state.get(path_str)
                    await self._embed_and_store(chunks, old_hash)

                    await db.execute(
                        "INSERT OR REPLACE INTO file_state (path, file_hash, mtime, indexed_at)"
                        " VALUES (?, ?, ?, ?)",
                        (path_str, current_hash, mtime, time.time()),
                    )
                    await db.commit()
                    counts["indexed"] += 1

                except Exception as e:
                    logger.error(f"Failed to index {file_path}: {e}", exc_info=True)
                    counts["errors"] += 1

                if _should_report:
                    await progress_callback(i + 1, total_files, file_path.name)

            # Purge deleted files
            for old_path, old_hash in old_state.items():
                if old_path not in seen:
                    self.store.delete_by_file_hash(old_hash)
                    await db.execute("DELETE FROM file_state WHERE path = ?", (old_path,))
                    counts["deleted"] += 1
            await db.commit()

        # Rebuild FTS index after changes
        if counts["indexed"] or counts["deleted"]:
            logger.info("Rebuilding FTS index...")
            self.store.ensure_fts_index()

        counts["total_files"] = len(all_files)
        return counts
