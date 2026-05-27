from __future__ import annotations

from pathlib import Path

PROSE_EXTENSIONS = {".md", ".txt", ".rst", ".tex", ".org", ".adoc"}


def parse_text(path: Path) -> tuple[str, dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    file_type = "text" if suffix in PROSE_EXTENSIONS else "code"
    lang = suffix.lstrip(".") if suffix else "text"
    return text, {"file_type": file_type, "lang": lang}
