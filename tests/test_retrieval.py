from __future__ import annotations

import pytest

from offline_rag.retrieval import _build_context, _rrf


def _make_items(prefix: str, n: int) -> list[dict]:
    return [
        {
            "id": f"{prefix}{i}",
            "file_path": f"docs/{prefix}{i}.md",
            "chunk_idx": 0,
            "text": f"Content of document {prefix}{i}. " * 10,
        }
        for i in range(n)
    ]


def test_rrf_shared_item_ranks_first():
    dense = _make_items("d", 5)
    sparse = _make_items("s", 5)
    # Plant a shared item at position 0 in dense and 1 in sparse
    shared = {"id": "shared", "file_path": "docs/shared.md", "chunk_idx": 0, "text": "shared"}
    dense.insert(0, shared)
    sparse.insert(1, shared)

    result = _rrf(dense, sparse, final_k=5)
    assert result[0]["id"] == "shared"


def test_rrf_no_duplicates_in_output():
    shared = {"id": "x", "file_path": "docs/x.md", "chunk_idx": 0, "text": "x"}
    dense = [shared] + _make_items("d", 4)
    sparse = [shared] + _make_items("s", 4)

    result = _rrf(dense, sparse, final_k=10)
    ids = [r["id"] for r in result]
    assert ids.count("x") == 1


def test_rrf_final_k_respected():
    dense = _make_items("d", 10)
    sparse = _make_items("s", 10)
    result = _rrf(dense, sparse, final_k=5)
    assert len(result) <= 5


def test_rrf_empty_lists():
    result = _rrf([], [], final_k=8)
    assert result == []


def test_rrf_one_empty_list():
    dense = _make_items("d", 5)
    result = _rrf(dense, [], final_k=5)
    assert len(result) == 5
    assert result[0]["id"] == "d0"


def test_build_context_respects_budget():
    chunks = [
        {"file_path": f"doc{i}.md", "chunk_idx": 0, "text": "word " * 400}
        for i in range(10)
    ]
    ctx, sources = _build_context(chunks, budget=500)
    assert len(ctx) // 4 <= 600  # generous slack
    assert len(sources) >= 1


def test_build_context_dedupes_sources():
    chunks = [
        {"file_path": "same.md", "chunk_idx": i, "text": f"chunk {i}"}
        for i in range(3)
    ]
    _, sources = _build_context(chunks, budget=10000)
    assert sources.count("same.md") == 1


def test_build_context_empty():
    ctx, sources = _build_context([], budget=6000)
    assert ctx == ""
    assert sources == []
