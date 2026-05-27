from __future__ import annotations

import pytest

from offline_rag.chunker import chunk_code, chunk_prose, make_chunks


def test_chunk_prose_produces_multiple_chunks():
    text = "\n\n".join([f"Paragraph {i}. " * 30 for i in range(10)])
    chunks = chunk_prose(text, size=100, overlap=20)
    assert len(chunks) > 1
    assert all(c.strip() for c in chunks)


def test_chunk_prose_respects_size():
    text = "\n\n".join([f"Para {i}. " * 50 for i in range(5)])
    chunks = chunk_prose(text, size=200, overlap=20)
    for c in chunks:
        assert len(c) // 4 <= 300  # some slack for overlap


def test_chunk_prose_single_short_text():
    text = "Hello world."
    chunks = chunk_prose(text, size=800, overlap=100)
    assert chunks == ["Hello world."]


def test_chunk_prose_empty():
    assert chunk_prose("", size=800, overlap=100) == []
    assert chunk_prose("   \n\n   ", size=800, overlap=100) == []


def test_chunk_code_python_ast():
    source = """\
def foo(x):
    return x + 1


def bar(y):
    return y * 2


class MyClass:
    def method(self):
        pass
"""
    chunks = chunk_code(source, lang="python")
    assert len(chunks) >= 2
    # At least one chunk should contain "def foo" or "def bar"
    combined = "\n".join(chunks)
    assert "def foo" in combined
    assert "def bar" in combined


def test_chunk_code_python_syntax_error_falls_back():
    bad_source = "\n".join([f"line {i}" for i in range(120)])
    chunks = chunk_code(bad_source, lang="python", size=60, overlap=10)
    assert len(chunks) > 1


def test_chunk_code_line_fallback():
    source = "\n".join([f"line {i}" for i in range(200)])
    chunks = chunk_code(source, lang="go", size=60, overlap=10)
    assert len(chunks) > 1
    assert all(c.strip() for c in chunks)


def test_make_chunks_returns_chunk_objects():
    text = "\n\n".join([f"Paragraph {i}. " * 20 for i in range(5)])
    chunks = make_chunks(
        text=text,
        file_path="docs/test.md",
        file_hash="abc123",
        mtime=1234567890.0,
        file_type="text",
        lang="md",
        config_size=100,
        config_overlap=20,
    )
    assert len(chunks) >= 1
    for c in chunks:
        assert c.file_path == "docs/test.md"
        assert c.file_hash == "abc123"
        assert c.id.startswith("abc123_")
        assert c.text.strip()


def test_make_chunks_code_type():
    source = "def foo():\n    pass\n\ndef bar():\n    pass\n"
    chunks = make_chunks(
        text=source,
        file_path="src/main.py",
        file_hash="def456",
        mtime=0.0,
        file_type="code",
        lang="python",
    )
    assert len(chunks) >= 1
    combined = "\n".join(c.text for c in chunks)
    assert "def foo" in combined or "def bar" in combined
