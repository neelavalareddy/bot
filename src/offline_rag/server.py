from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal

from fastapi import BackgroundTasks, FastAPI, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from offline_rag.config import Config, load_config
from offline_rag.indexer import FileIndexer
from offline_rag.ollama_client import OllamaClient
from offline_rag.retrieval import SYSTEM_PROMPT, retrieve
from offline_rag.store import VectorStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenAI-compatible request/response models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None


# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------

_state: dict = {}
_index_jobs: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    ollama = OllamaClient(config.ollama_base_url)
    store = VectorStore(f"{config.data_dir}/lancedb")
    _state.update(config=config, ollama=ollama, store=store)
    yield
    await ollama.aclose()


app = FastAPI(title="Offline RAG", lifespan=lifespan)


# ---------------------------------------------------------------------------
# /v1/chat/completions
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    rag: bool = Query(default=True, description="Set ?rag=false to bypass retrieval"),
):
    config: Config = _state["config"]
    ollama: OllamaClient = _state["ollama"]
    store: VectorStore = _state["store"]

    messages = [m.model_dump() for m in request.messages]

    # Find last user message index
    last_user_idx: int | None = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i]["role"] == "user":
            last_user_idx = i
            break

    if rag and last_user_idx is not None:
        user_query = messages[last_user_idx]["content"]
        try:
            result = await retrieve(user_query, config, store, ollama)
            if result.context_text:
                augmented = (
                    f"Context from your documents:\n\n{result.context_text}"
                    f"\n\n---\n\nQuestion: {user_query}"
                )
                messages[last_user_idx] = {"role": "user", "content": augmented}
                if not messages or messages[0]["role"] != "system":
                    messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        except Exception as e:
            logger.warning(f"Retrieval failed, falling back to plain LLM: {e}")

    options: dict = {}
    if request.temperature is not None:
        options["temperature"] = request.temperature
    if request.top_p is not None:
        options["top_p"] = request.top_p

    model = request.model

    if request.stream:
        return StreamingResponse(
            _stream(model, messages, options),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    # Non-streaming: collect all chunks
    content_parts: list[str] = []
    async for delta in ollama.chat_stream(model, messages, options):
        content_parts.append(delta)
    content = "".join(content_parts)

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": -1, "completion_tokens": -1, "total_tokens": -1},
    }


async def _stream(
    model: str, messages: list[dict], options: dict
) -> AsyncIterator[bytes]:
    ollama: OllamaClient = _state["ollama"]
    req_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    created = int(time.time())

    async for delta in ollama.chat_stream(model, messages, options):
        chunk = {
            "id": req_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n".encode()

    stop_chunk = {
        "id": req_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(stop_chunk)}\n\n".encode()
    yield b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# /v1/models  — proxy Ollama's tag list
# ---------------------------------------------------------------------------

@app.get("/v1/models")
async def list_models():
    ollama: OllamaClient = _state["ollama"]
    try:
        models = await ollama.list_models()
    except Exception as e:
        logger.warning(f"Failed to list Ollama models: {e}")
        models = []
    return {
        "object": "list",
        "data": [
            {
                "id": m["name"],
                "object": "model",
                "created": 0,
                "owned_by": "ollama",
            }
            for m in models
        ],
    }


# ---------------------------------------------------------------------------
# /index  — trigger background reindex
# ---------------------------------------------------------------------------

@app.post("/index")
async def trigger_index(
    background_tasks: BackgroundTasks,
    paths: list[str] | None = None,
):
    job_id = uuid.uuid4().hex[:8]
    _index_jobs[job_id] = {"status": "running", "started_at": time.time()}
    background_tasks.add_task(_run_index, job_id, paths)
    return {"job_id": job_id, "status": "started"}


async def _run_index(job_id: str, paths: list[str] | None) -> None:
    config: Config = _state["config"]
    ollama: OllamaClient = _state["ollama"]
    store: VectorStore = _state["store"]
    try:
        indexer = FileIndexer(config, store, ollama)
        result = await indexer.index_paths(paths)
        _index_jobs[job_id] = {"status": "done", "result": result}
    except Exception as e:
        logger.error(f"Background index job {job_id} failed: {e}", exc_info=True)
        _index_jobs[job_id] = {"status": "error", "error": str(e)}


@app.get("/index/{job_id}")
async def index_status(job_id: str):
    return _index_jobs.get(job_id, {"status": "not_found"})


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

@app.get("/status")
async def status():
    config: Config = _state["config"]
    store: VectorStore = _state["store"]
    from pathlib import Path

    stats = store.get_stats()
    db_path = Path(config.data_dir) / "lancedb"
    disk_bytes = (
        sum(f.stat().st_size for f in db_path.rglob("*") if f.is_file())
        if db_path.exists()
        else 0
    )
    state_db = Path(config.data_dir) / "state.db"
    disk_bytes += state_db.stat().st_size if state_db.exists() else 0

    return {
        "file_count": stats["unique_files"],
        "chunk_count": stats["total_chunks"],
        "disk_mb": round(disk_bytes / 1024 / 1024, 2),
        "ollama_url": config.ollama_base_url,
        "embedding_model": config.embedding_model,
    }
