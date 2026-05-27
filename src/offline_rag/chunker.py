from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field


@dataclass
class Chunk:
    file_path: str
    file_hash: str
    chunk_idx: int
    file_type: str
    mtime: float
    parent_heading: str
    lang: str
    text: str
    id: str = ""
    vector: list[float] | None = None

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"{self.file_hash}_{self.chunk_idx}"


def _tok(text: str) -> int:
    """Approximate token count: 1 token ≈ 4 chars."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Prose chunker
# ---------------------------------------------------------------------------

def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _tail_overlap(parts: list[str], overlap_tokens: int) -> str:
    """Return the last ~overlap_tokens worth of text from parts list."""
    collected: list[str] = []
    budget = overlap_tokens
    for part in reversed(parts):
        pt = _tok(part)
        if pt <= budget:
            collected.insert(0, part)
            budget -= pt
        else:
            words = part.split()
            take = max(1, int(budget * 4 / max(len(part) / len(words), 1))) if words else 0
            if take:
                collected.insert(0, " ".join(words[-take:]))
            break
    return "\n\n".join(collected)


def chunk_prose(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    """Paragraph-aware prose chunking."""
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_tok = 0

    def flush() -> None:
        nonlocal current, current_tok
        if current:
            chunks.append("\n\n".join(current))
            tail = _tail_overlap(current, overlap)
            current = [tail] if tail else []
            current_tok = _tok(tail) if tail else 0

    for para in paragraphs:
        pt = _tok(para)

        if pt > size:
            flush()
            # Split long paragraph on sentence boundaries
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sent in sentences:
                st = _tok(sent)
                if current_tok + st > size and current:
                    flush()
                current.append(sent)
                current_tok += st
            flush()
        elif current_tok + pt > size:
            flush()
            current.append(para)
            current_tok += pt
        else:
            current.append(para)
            current_tok += pt

    if current:
        chunks.append("\n\n".join(current))

    return [c for c in chunks if c.strip()]


# ---------------------------------------------------------------------------
# Code chunker
# ---------------------------------------------------------------------------

def _python_boundaries(source: str) -> list[tuple[int, int]]:
    """Extract (start_line, end_line) for top-level functions and classes."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    items = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            items.append((node.lineno - 1, node.end_lineno))
    return sorted(items)


_JS_FUNC = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+\w+"
    r"|^(?:export\s+)?(?:default\s+)?class\s+\w+"
    r"|^(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?(?:function|\(|async\s*\()",
    re.MULTILINE,
)


def _js_boundaries(source: str) -> list[tuple[int, int]]:
    lines = source.splitlines()
    items = []
    for m in _JS_FUNC.finditer(source):
        start = source[: m.start()].count("\n")
        depth = 0
        end = start
        for j in range(start, len(lines)):
            depth += lines[j].count("{") - lines[j].count("}")
            if j > start and depth <= 0:
                end = j + 1
                break
        items.append((start, end))
    return sorted(items)


_GENERIC_FUNC = re.compile(
    r"^(?:func|def|fn|public|private|protected|static|void|int|string|bool|auto)\s+\w+\s*\(",
    re.MULTILINE,
)


def _generic_boundaries(source: str) -> list[tuple[int, int]]:
    lines = source.splitlines()
    items = []
    for m in _GENERIC_FUNC.finditer(source):
        start = source[: m.start()].count("\n")
        depth = 0
        end = min(start + 80, len(lines))
        for j in range(start, len(lines)):
            depth += lines[j].count("{") - lines[j].count("}")
            if j > start and depth <= 0:
                end = j + 1
                break
        items.append((start, end))
    return sorted(items)


def chunk_code(
    text: str,
    lang: str = "text",
    size: int = 60,
    overlap: int = 10,
) -> list[str]:
    """AST-aware chunking for Python/JS/TS; line-based fallback."""
    lines = text.splitlines()

    if lang == "python":
        boundaries = _python_boundaries(text)
    elif lang in ("javascript", "typescript"):
        boundaries = _js_boundaries(text)
    elif lang in ("go", "rust", "java", "c", "cpp", "csharp", "kotlin", "swift"):
        boundaries = _generic_boundaries(text)
    else:
        boundaries = []

    if boundaries:
        chunks: list[str] = []
        covered: set[int] = set()

        for start, end in boundaries:
            covered.update(range(start, end))
            block = lines[start:end]
            if len(block) > size * 2:
                for i in range(0, len(block), size):
                    c = "\n".join(block[i : i + size + overlap])
                    if c.strip():
                        chunks.append(c)
            else:
                c = "\n".join(block)
                if c.strip():
                    chunks.append(c)

        # Module-level code not covered by any boundary
        module_lines = [l for i, l in enumerate(lines) if i not in covered]
        module_text = "\n".join(module_lines).strip()
        if module_text:
            chunks.insert(0, module_text)

        return [c for c in chunks if c.strip()]

    # Fallback: line-based chunks
    chunks = []
    for i in range(0, len(lines), size):
        c = "\n".join(lines[i : i + size + overlap])
        if c.strip():
            chunks.append(c)
    return chunks


# ---------------------------------------------------------------------------
# Extract markdown headings per chunk
# ---------------------------------------------------------------------------

def _headings_at_positions(text: str) -> list[tuple[int, str]]:
    """Return (char_offset, heading_text) for each ATX heading."""
    result = []
    for m in re.finditer(r"^(#{1,6})\s+(.+)$", text, re.MULTILINE):
        result.append((m.start(), m.group(2).strip()))
    return result


def _heading_for_offset(headings: list[tuple[int, str]], offset: int) -> str:
    best = ""
    for pos, h in headings:
        if pos <= offset:
            best = h
        else:
            break
    return best


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def make_chunks(
    text: str,
    file_path: str,
    file_hash: str,
    mtime: float,
    file_type: str,
    lang: str,
    config_size: int = 800,
    config_overlap: int = 100,
) -> list[Chunk]:
    """Dispatch to prose or code chunker and produce Chunk objects."""
    is_code = file_type == "code"

    if is_code:
        raw = chunk_code(text, lang=lang, size=60, overlap=10)
    else:
        raw = chunk_prose(text, size=config_size, overlap=config_overlap)

    headings = _headings_at_positions(text) if not is_code else []

    chunks: list[Chunk] = []
    char_pos = 0
    for idx, chunk_text in enumerate(raw):
        pos = text.find(chunk_text[:40], char_pos)
        if pos != -1:
            char_pos = pos
        heading = _heading_for_offset(headings, char_pos) if headings else ""

        chunks.append(
            Chunk(
                id=f"{file_hash}_{idx}",
                file_path=file_path,
                file_hash=file_hash,
                chunk_idx=idx,
                file_type=file_type,
                mtime=mtime,
                parent_heading=heading,
                lang=lang,
                text=chunk_text,
            )
        )

    return chunks
