from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal

from fastapi import BackgroundTasks, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from pathlib import Path

from offline_rag.agent import run_agent
from offline_rag.agent.skills import SkillRegistry
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
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""

    model_config = ConfigDict(extra="ignore")


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None

    model_config = ConfigDict(extra="ignore")


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
    skill_registry = SkillRegistry(Path(config.data_dir) / "skills")
    _state.update(config=config, ollama=ollama, store=store, skill_registry=skill_registry)
    yield
    await ollama.aclose()


app = FastAPI(title="Universal Bot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(model: str, content: str) -> dict:
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


async def _agent_gen_safe(messages, model, config, ollama, store, skill_registry=None):
    """Wraps run_agent to catch exceptions and yield them as visible error messages."""
    try:
        async for chunk in run_agent(
            messages, model, config, ollama, store, skill_registry=skill_registry
        ):
            yield chunk
    except Exception as exc:
        logger.exception("Agent error during streaming")
        yield f"\n\n❌ **Agent error:** {exc}"


async def _sse(
    source: AsyncIterator[str],
    model: str,
) -> AsyncIterator[bytes]:
    req_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    created = int(time.time())

    async for chunk in source:
        data = {
            "id": req_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(data)}\n\n".encode()

    stop = {
        "id": req_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(stop)}\n\n".encode()
    yield b"data: [DONE]\n\n"


async def _legacy_stream(
    model: str, messages: list[dict], options: dict
) -> AsyncIterator[bytes]:
    """Original plain-ollama streaming path (no agent, no tools)."""
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
# /v1/chat/completions
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    agent: bool = Query(default=True, description="Route through the universal agent (default true). Set ?agent=false for plain RAG."),
    rag: bool = Query(default=True, description="Apply RAG retrieval when agent=false."),
):
    config: Config = _state["config"]
    ollama: OllamaClient = _state["ollama"]
    store: VectorStore = _state["store"]

    messages = [m.model_dump() for m in request.messages]
    model = request.model

    # ------------------------------------------------------------------
    # Agent path (default)
    # ------------------------------------------------------------------
    if agent:
        skill_registry: SkillRegistry = _state["skill_registry"]
        agent_gen = _agent_gen_safe(messages, model, config, ollama, store, skill_registry)

        if request.stream:
            return StreamingResponse(
                _sse(agent_gen, model),
                media_type="text/event-stream",
                headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
            )

        parts: list[str] = []
        async for chunk in agent_gen:
            parts.append(chunk)
        return _make_response(model, "".join(parts))

    # ------------------------------------------------------------------
    # Legacy RAG-only path (?agent=false)
    # ------------------------------------------------------------------
    if rag:
        last_user_idx: int | None = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]["role"] == "user":
                last_user_idx = i
                break

        if last_user_idx is not None:
            user_query = messages[last_user_idx]["content"]
            try:
                result = await retrieve(user_query, config, store, ollama)
                if result.context_text:
                    messages[last_user_idx] = {
                        "role": "user",
                        "content": (
                            f"Context from your documents:\n\n{result.context_text}"
                            f"\n\n---\n\nQuestion: {user_query}"
                        ),
                    }
                    if not messages or messages[0]["role"] != "system":
                        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
            except Exception as e:
                logger.warning(f"Retrieval failed, falling back to plain LLM: {e}")

    options: dict = {}
    if request.temperature is not None:
        options["temperature"] = request.temperature
    if request.top_p is not None:
        options["top_p"] = request.top_p

    if request.stream:
        return StreamingResponse(
            _legacy_stream(model, messages, options),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    content_parts: list[str] = []
    async for delta in ollama.chat_stream(model, messages, options):
        content_parts.append(delta)
    return _make_response(model, "".join(content_parts))


# ---------------------------------------------------------------------------
# /v1/models
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
            {"id": m["name"], "object": "model", "created": 0, "owned_by": "ollama"}
            for m in models
        ],
    }


# ---------------------------------------------------------------------------
# /index  — background reindex
# ---------------------------------------------------------------------------

class IndexRequest(BaseModel):
    paths: list[str] | None = None


@app.post("/index")
async def trigger_index(background_tasks: BackgroundTasks, req: IndexRequest):
    job_id = uuid.uuid4().hex[:8]
    _index_jobs[job_id] = {"status": "running", "started_at": time.time()}
    background_tasks.add_task(_run_index, job_id, req.paths)
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
