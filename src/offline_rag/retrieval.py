from __future__ import annotations

import logging
from dataclasses import dataclass, field

from offline_rag.config import Config
from offline_rag.ollama_client import OllamaClient
from offline_rag.store import VectorStore

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a helpful assistant with access to the user's personal documents.\n"
    "When answering, cite your sources using the bracketed path shown in the context, "
    "e.g. [source: path/to/file.md#chunk_0].\n"
    "If you cannot find relevant information in the provided context, say so clearly "
    "rather than hallucinating an answer.\n"
    "Be concise and precise."
)


@dataclass
class RetrievalResult:
    chunks: list[dict] = field(default_factory=list)
    context_text: str = ""
    sources: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------

def _rrf(
    dense: list[dict],
    sparse: list[dict],
    k: int = 60,
    final_k: int = 8,
) -> list[dict]:
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}

    for rank, item in enumerate(dense):
        iid = item["id"]
        scores[iid] = scores.get(iid, 0.0) + 1.0 / (k + rank + 1)
        items[iid] = item

    for rank, item in enumerate(sparse):
        iid = item["id"]
        scores[iid] = scores.get(iid, 0.0) + 1.0 / (k + rank + 1)
        items[iid] = item

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [items[iid] for iid, _ in ranked[:final_k]]


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def _build_context(chunks: list[dict], budget: int) -> tuple[str, list[str]]:
    parts: list[str] = []
    sources: list[str] = []
    used = 0

    for chunk in chunks:
        label = f"[source: {chunk['file_path']}#chunk_{chunk['chunk_idx']}]"
        block = f"{label}\n{chunk['text']}"
        block_tok = len(block) // 4

        if used + block_tok > budget:
            break

        parts.append(block)
        fp = chunk["file_path"]
        if fp not in sources:
            sources.append(fp)
        used += block_tok

    return "\n\n---\n\n".join(parts), sources


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def retrieve(
    query: str,
    config: Config,
    store: VectorStore,
    ollama: OllamaClient,
) -> RetrievalResult:
    try:
        embeddings = await ollama.embed_batch(config.embedding_model, [query])
        query_vector = embeddings[0]
    except Exception as e:
        logger.error(f"Embedding query failed: {e}")
        return RetrievalResult()

    dense = store.vector_search(query_vector, k=config.top_k_dense)
    sparse = store.fts_search(query, k=config.top_k_sparse)

    if not dense and not sparse:
        return RetrievalResult()

    fused = _rrf(dense, sparse, final_k=config.final_k)
    context_text, sources = _build_context(fused, config.context_budget_tokens)

    return RetrievalResult(chunks=fused, context_text=context_text, sources=sources)
